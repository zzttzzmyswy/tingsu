package agent

import (
	"context"
	"errors"
	"strings"
	"sync"
)

type Hooks struct {
	Position func(string) float64
	Context  func(string) (M, error)
	Tool     func(string, string, M) (any, error)
	Pending  func(string) ([]M, error)
	Reminder func(string, float64, float64, string, []M) (bool, error)
	Event    func(string, string, float64, string, string, []string) error
	Frame    func(string, float64) string
	Changed  func()
	Stream   func(string)
	Notify   func(string, string)
}
type submission struct {
	text  string
	files []string
}
type task struct {
	context.Context
	stop context.CancelFunc
	mid  string
	c    *Client
	submission
	gen               uint64
	follow, automatic bool
	partial           string
}
type Scheduler struct {
	mu sync.Mutex
	// gate orders cancellation against board mutations and stale reply delivery.
	gate                 sync.Mutex
	h                    Hooks
	mid                  string
	c                    *Client
	gen                  uint64
	auto, check, pending bool
	last                 float64
	next                 []submission
	active               *task
}

const autoPrompt = "观察最新发言，有重要结论、待办或未决问题时更新便签，避免重复。用简短中文反馈。"
const followPrompt = "开场跟进。请根据上下文「本次开场跟进」用简短中文提醒与会者跟进往期问题和待办，说明来自哪场会议，必要时询问进展。本轮只做提醒。以当前提供的状态为准。"

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
	s.gen++
	s.active = nil
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
	s.mu.Unlock()
	s.start()
}
func (s *Scheduler) Cancel() {
	s.gate.Lock()
	s.mu.Lock()
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
	if !v {
		s.pending = false
		if s.active != nil && s.active.follow {
			s.active.stop()
		}
	}
	s.mu.Unlock()
	s.start()
}
func (s *Scheduler) Transcribed(mid string, end float64, text bool) {
	s.mu.Lock()
	if s.mid == mid && s.auto && text && end-s.last >= 28 {
		s.last = end
		s.pending = true
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
		text = "请阅读这些会议材料。"
	}
	s.gate.Lock()
	s.mu.Lock()
	mid := s.mid
	ok := s.c != nil
	s.mu.Unlock()
	if !ok {
		s.gate.Unlock()
		return errors.New("请先连接 AI 会话")
	}
	if e := s.h.Event(mid, "user", s.h.Position(mid), "我", text, files); e != nil {
		s.gate.Unlock()
		return e
	}
	s.mu.Lock()
	s.next = append(s.next, submission{text, append([]string(nil), files...)})
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
	t := &task{mid: s.mid, c: s.c, gen: s.gen}
	switch {
	case len(s.next) > 0:
		t.submission = s.next[0]
		s.next = s.next[1:]
	case s.check:
		t.text = followPrompt
		t.follow = true
		s.check = false
	case s.pending:
		t.text = autoPrompt
		t.automatic = true
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
	if (!t.follow || len(items) > 0) && e == nil && t.Err() == nil {
		s.changed()
		var meeting M
		meeting, e = s.h.Context(t.mid)
		if e == nil {
			frame := ""
			if t.follow {
				if meeting == nil {
					meeting = M{}
				}
				meeting["本次开场跟进"] = items
			} else if s.h.Frame != nil {
				frame = s.h.Frame(t.mid, at)
			}
			reply, e = t.c.Send(t.Context, t.mid, t.text, t.files, frame, meeting, func(piece string) {
				if t.follow {
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
		}
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
