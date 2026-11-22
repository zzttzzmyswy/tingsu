package agent

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"github.com/zzttzzmyswy/tingsu/internal/store"
)

type Hooks struct {
	Position func(string) float64
	Context  func(string, string, string) (store.AgentBatch, error)
	Tool     func(string, string, M) (any, error)
	Pending  func(string) ([]M, error)
	Reminder func(string, float64, float64, string, []M) (bool, error)
	Event    func(string, string, float64, string, string, []string) error
	Frame    func(string, float64) string
	Changed  func()
	Stream   func(string)
	Notify   func(string, string)
}
type stagedFiles struct {
	files []material
	err   error
}
type submission struct {
	text  string
	ready <-chan stagedFiles
}
type task struct {
	context.Context
	stop context.CancelFunc
	mid  string
	c    *Client
	submission
	follow    bool
	automatic bool
	partial   string
}
type Scheduler struct {
	mu sync.Mutex
	// gate orders cancellation against board mutations and stale reply delivery.
	gate                 sync.Mutex
	h                    Hooks
	mid                  string
	c                    *Client
	auto, check, pending bool
	last                 float64
	tail                 *time.Timer
	next                 []submission
	active               *task
}

const autoPrompt = "结合已有会话逐条理解新增发言及本轮画面。保留重要观点、分歧、疑问、决定和行动的依据；仅在内容有实质变化时维护便签。简短反馈。"
const followPrompt = "开场跟进：只读「本次开场跟进」中的未结项。按来源会议简述事项和当前状态，必要时询问进展。"

func NewScheduler(h Hooks, auto bool) *Scheduler { return &Scheduler{h: h, auto: auto} }
func (s *Scheduler) State() (bool, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.active != nil, len(s.next)
}
func (s *Scheduler) Detach() {
	s.gate.Lock()
	s.mu.Lock()
	if s.active != nil {
		s.active.stop()
	}
	s.active = nil
	s.clearTail()
	s.mid = ""
	s.c = nil
	s.next = nil
	s.pending = false
	s.check = false
	s.mu.Unlock()
	s.gate.Unlock()
	s.stream("")
}
func (s *Scheduler) Bind(mid string, c *Client, at float64) {
	s.Detach()
	s.mu.Lock()
	s.mid = mid
	s.c = c
	s.last = at - 28
	s.check = s.auto
	s.pending = s.auto
	s.mu.Unlock()
	s.start()
}
func (s *Scheduler) Cancel() {
	s.gate.Lock()
	s.mu.Lock()
	s.clearTail()
	s.check = false
	s.pending = false
	if s.active != nil {
		s.active.stop()
	}
	s.mu.Unlock()
	s.gate.Unlock()
}
func (s *Scheduler) SetAutomatic(v bool) {
	s.mu.Lock()
	if s.auto == v {
		s.mu.Unlock()
		return
	}
	s.auto = v
	s.check = v
	s.pending = v
	if !v {
		s.pending = false
		s.clearTail()
		if s.active != nil && (s.active.follow || s.active.automatic) {
			s.active.stop()
		}
	}
	s.mu.Unlock()
	s.start()
}

// clearTail is called under mu. The callback also checks the timer identity.
func (s *Scheduler) clearTail() {
	if s.tail != nil {
		s.tail.Stop()
		s.tail = nil
	}
}
func (s *Scheduler) Flush(mid string) {
	s.mu.Lock()
	if s.auto && s.mid == mid {
		s.clearTail()
		s.pending = true
	}
	s.mu.Unlock()
	s.start()
}
func (s *Scheduler) Transcribed(mid string, end float64, text bool) {
	s.mu.Lock()
	if s.mid == mid && s.auto && text {
		if end-s.last >= 28 {
			s.clearTail()
			s.last = end
			s.pending = true
		} else if s.tail == nil {
			var timer *time.Timer
			timer = time.AfterFunc(28*time.Second, func() {
				s.mu.Lock()
				if s.tail == timer {
					s.tail = nil
					s.pending = true
				}
				s.mu.Unlock()
				s.start()
			})
			s.tail = timer
		}
	}
	s.mu.Unlock()
	s.start()
}
func (s *Scheduler) Submit(text string, files []string) error {
	text = strings.TrimSpace(text)
	if text == "" {
		if len(files) == 0 {
			return errors.New("请输入消息或添加材料")
		}
		text = "请阅读本次附件，概括与本场会议有关的要点并标明来源文件。"
	}
	s.gate.Lock()
	s.mu.Lock()
	mid := s.mid
	client := s.c
	s.mu.Unlock()
	if client == nil {
		s.gate.Unlock()
		return errors.New("请先连接 AI 会话")
	}
	if e := s.h.Event(mid, "user", s.h.Position(mid), "我", text, files); e != nil {
		s.gate.Unlock()
		return e
	}
	entry := submission{text: text}
	if len(files) > 0 {
		ready := make(chan stagedFiles, 1)
		entry.ready = ready
		paths := append([]string(nil), files...)
		go func() { originals, err := client.stage(client.life, mid, paths); ready <- stagedFiles{originals, err} }()
	}
	s.mu.Lock()
	s.next = append(s.next, entry)
	s.mu.Unlock()
	s.gate.Unlock()
	s.start()
	s.changed()
	return nil
}
func (s *Scheduler) start() {
	s.mu.Lock()
	if s.active != nil || s.c == nil {
		s.mu.Unlock()
		return
	}
	t := &task{mid: s.mid, c: s.c}
	switch {
	case len(s.next) > 0:
		t.submission = s.next[0]
		s.next = s.next[1:]
	case s.check:
		t.text = followPrompt
		t.follow = true
		s.check = false
	case s.pending:
		t.automatic = true
		t.text = autoPrompt
		s.pending = false
	default:
		s.mu.Unlock()
		return
	}
	t.Context, t.stop = context.WithCancel(context.Background())
	s.active = t
	s.mu.Unlock()
	go s.run(t)
}
func (s *Scheduler) stream(text string) {
	if s.h.Stream != nil {
		s.h.Stream(text)
	}
}
func (s *Scheduler) changed() {
	if s.h.Changed != nil {
		s.h.Changed()
	}
}
func (s *Scheduler) notify(v, level string) {
	if s.h.Notify != nil {
		s.h.Notify(v, level)
	}
}
func (s *Scheduler) run(t *task) {
	at := s.h.Position(t.mid)
	var items []M
	var e error
	var reply string
	if t.follow && s.h.Pending != nil {
		items, e = s.h.Pending(t.mid)
	}
	var originals []material
	if t.ready != nil {
		select {
		case snapshot := <-t.ready:
			originals, e = snapshot.files, snapshot.err
		case <-t.Done():
			e = t.Err()
		}
	}
	if (!t.follow || len(items) > 0) && e == nil && t.Err() == nil {
		s.changed()
		reply, e = s.deliver(t, at, items, originals)

	}
	s.gate.Lock()
	s.mu.Lock()
	if s.active != t {
		s.mu.Unlock()
		s.gate.Unlock()
		t.stop()
		return
	}
	cancelled := t.Err() != nil
	s.mu.Unlock()
	if cancelled {
		reply = ""
		if strings.TrimSpace(t.partial) != "" {
			reply = t.partial + "\n（回复已停止）"
		}
		s.notify("已停止本次 AI 回复", "ok")
	} else if e != nil {
		s.notify(cut(e.Error(), 800), "error")
		_ = s.h.Event(t.mid, "system", at, "系统", "AI 回复失败："+cut(e.Error(), 700), nil)
	}
	recheck := false
	if strings.TrimSpace(reply) != "" {
		end := s.h.Position(t.mid)
		if t.follow {
			if !cancelled && e == nil && s.h.Reminder != nil {
				var saved bool
				saved, e = s.h.Reminder(t.mid, at, end, reply, items)
				recheck = e == nil && !saved
			}
		} else {
			e = s.h.Event(t.mid, "assistant", end, "听序 AI", reply, nil)
		}
		if e != nil {
			s.notify(e.Error(), "error")
		}
	}
	t.stop()
	s.mu.Lock()
	s.active = nil
	if cancelled || e != nil {
		s.pending = false
	}
	if recheck && s.auto {
		s.check = true
	}
	s.mu.Unlock()
	s.stream("")
	s.gate.Unlock()
	s.changed()
	s.start()
}

func (s *Scheduler) deliver(t *task, at float64, items []M, originals []material) (string, error) {
	for {
		if err := t.Err(); err != nil {
			return "", err
		}
		batch, err := s.h.Context(t.mid, t.c.cfg.Provider, t.c.SessionID())
		if err != nil {
			return "", err
		}
		meeting := batch.Context
		text, files, frame := t.text, originals, ""
		intermediate := batch.More && !t.follow
		switch {
		case t.follow:
			meeting = M{"本场会议ID": t.mid, "会议": meeting["会议"], "主题": meeting["主题"], "本次开场跟进": items}
		case t.automatic && !batch.HasSpeech:
			return "", nil
		case intermediate:
			text = "按顺序分析这批新增原文，后续还有输入。保留判断依据，等收到本轮任务再统一作答。"
			files = nil
		case s.h.Frame != nil:
			frame = s.h.Frame(t.mid, at)
		}
		reply, err := t.c.send(t.Context, t.mid, text, files, frame, meeting, func(piece string) {
			if t.follow || intermediate {
				return
			}
			s.gate.Lock()
			defer s.gate.Unlock()
			s.mu.Lock()
			ok := s.active == t && t.Err() == nil
			if ok {
				t.partial += piece
			}
			v := t.partial
			s.mu.Unlock()
			if ok {
				s.stream(v)
			}
		}, func(name string, a M) (any, error) {
			s.gate.Lock()
			defer s.gate.Unlock()
			if t.Err() != nil {
				return nil, t.Err()
			}
			write := !strings.HasPrefix(name, "read_") && !strings.HasPrefix(name, "list_")
			if write && t.follow {
				return nil, errors.New("开场提醒只读取看板")
			}
			out, e := s.h.Tool(t.mid, name, a)
			if write && e == nil {
				s.changed()
			}
			return out, e
		})
		if err != nil || t.follow {
			return reply, err
		}
		s.gate.Lock()
		err = t.Err()
		if err == nil {
			err = batch.Acknowledge()
		}
		s.gate.Unlock()
		if err != nil || !intermediate {
			return reply, err
		}
	}
}
