package desktop

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"time"

	"github.com/zzttzzmyswy/tingsu/internal/agent"
	"github.com/zzttzzmyswy/tingsu/internal/media"
	"github.com/zzttzzmyswy/tingsu/internal/screen"
	"github.com/zzttzzmyswy/tingsu/internal/store"
	"github.com/zzttzzmyswy/tingsu/internal/transcription"
)

// Desktop state and native dialog callbacks are owned by the host event loop.
// Workers post results to that loop; frame requests use an immutable snapshot.
type frameRequest struct {
	meeting string
	live    *screen.Session
	version int
}
type bridge struct {
	*host
	db                                  *store.Store
	settings                            settings
	data, mid                           string
	ready                               bool
	limit                               int
	asr                                 *transcription.Service
	turns                               *agent.Scheduler
	sources                             []media.Source
	files                               []string
	client                              *agent.Client
	provider, session, status, loginURL string
	disconnect                          context.CancelFunc
	generation                          int
	sessions                            []M
	catalogs                            M
	live                                *screen.Session
	screenVersion                       int
	windows                             map[string]screen.Window
	frame, preview                      string
	frameRequest                        atomic.Pointer[frameRequest]
}

func newBridge(h *host, data string) (*bridge, error) {
	config, e := readSettings()
	if e != nil {
		return nil, e
	}
	db, e := store.Open(filepath.Join(data, "studio.sqlite3"))
	if e != nil {
		return nil, e
	}
	b := &bridge{host: h, db: db, data: data, settings: config, mid: str(config.values["selected"]), limit: 120, status: "offline", catalogs: M{}, sessions: []M{}}
	if db.Meeting(b.mid) == nil {
		b.mid = db.LatestMeetingID()
	}
	b.asr = transcription.New(db, filepath.Join(data, "audio"), transcription.Callbacks{
		Changed: h.changed, Transcribed: func(mid string, end float64, speech bool) {
			h.post(func() {
				if mid == b.mid {
					b.turns.Transcribed(mid, end, speech)
					b.changed()
				}
			})
		},
		Failed: func(mid, msg string) {
			h.post(func() {
				if mid == b.mid {
					b.notice(msg, "error")
				}
			})
		},
		Completed: func(mid string) {
			h.post(func() {
				if mid == b.mid {
					b.notice("已完成本次转写", "ok")
					b.changed()
				}
			})
		},
	})
	b.turns = agent.NewScheduler(agent.Hooks{Position: func(mid string) float64 {
		if t, ok := b.asr.Position(); ok {
			return t
		}
		return number(db.Meeting(mid)["duration"])
	},
		Context: func(mid string) (M, error) { return db.AgentContext(mid), nil }, Pending: func(mid string) ([]M, error) { return db.PendingTopicFollowups(mid), nil },
		Reminder: db.SaveTopicReminder, Tool: func(mid, name string, args M) (any, error) { return db.Tool(mid, name, args, "AI") },
		Event: func(mid, kind string, at float64, speaker, text string, files []string) error {
			return db.Event(mid, kind, at, at, speaker, text, files)
		},
		Frame: b.turnFrame, Changed: h.changed, Stream: func(text string) { h.post(func() { b.event("delta", text) }) }, Notify: func(text, level string) { h.post(func() { b.notice(text, level) }) },
	}, truth(config.values["auto"]))
	return b, nil
}
func (b *bridge) close()                   { b.cancel(); b.detach(); b.stopScreen(); b.asr.Close(); b.db.Close() }
func (b *bridge) notice(msg, level string) { b.signal("notice", msg, level) }
func (b *bridge) event(kind, value string) { b.signal("event", kind, value) }
func (b *bridge) fail(e error) {
	m := []rune(e.Error())
	if len(m) > 800 {
		m = m[:800]
	}
	b.notice(string(m), "error")
	b.changed()
}
func (b *bridge) active() bool {
	s, q := b.turns.State()
	return s || q > 0 || b.asr.Status() != "idle"
}
func (b *bridge) canSwitch() error {
	if err := b.asr.EnsureIdle(); err != nil {
		return err
	}
	if s, q := b.turns.State(); s || q > 0 {
		return errors.New("请先结束当前 AI 回复，再切换会议。")
	}
	return nil
}
func (b *bridge) current() M { return b.db.Meeting(b.mid) }
func sourceKey(s media.Source) string {
	if s.Kind == "file" {
		return s.ID
	}
	return s.Kind + ":" + s.ID
}
func (b *bridge) publish() {
	state, err := b.db.Snapshot(b.mid, b.limit)
	if err != nil {
		b.fail(err)
		return
	}
	sources := []M{}
	for _, s := range b.sources {
		sources = append(sources, M{"id": sourceKey(s), "kind": s.Kind, "label": s.Label})
	}
	files := []string{}
	for _, p := range b.files {
		files = append(files, filepath.Base(p))
	}
	workspace := ""
	if b.status == "ready" && b.provider != "openai" {
		workspace = b.client.Workspace()
	}
	sending, queued := b.turns.State()
	for k, v := range (M{"loginLink": b.loginURL != "", "fileSource": filepath.IsAbs(str(b.current()["source"])), "settings": b.settings.public(), "providers": providers, "agentCatalogs": b.catalogs,
		"sources": sources, "capture": b.asr.Status(), "agent": b.status, "agentName": b.provider,
		"agentWorkspace": workspace, "session": b.session, "sessions": b.sessions, "sending": sending, "queued": queued, "files": files, "frame": b.preview,
		"screen": b.live != nil, "xwayland": screen.Desktop() == "wayland" && os.Getenv("DISPLAY") != "", "windowsDesktop": runtime.GOOS == "windows"}) {
		state[k] = v
	}
	b.signal("state", encoded(state))
}
func (b *bridge) loadSources() {
	go func() {
		s, e := media.Sources(b.ctx)
		b.post(func() {
			if e != nil {
				b.fail(e)
			}
			b.sources = s
			b.changed()
		})
	}()
}
func (b *bridge) source(id string) (media.Source, error) {
	for _, s := range b.sources {
		if sourceKey(s) == id {
			return s, nil
		}
	}
	return media.Source{}, errors.New("请选择可用的采集来源，或刷新音频设备。")
}
func (b *bridge) change(mid string) error {
	if e := b.canSwitch(); e != nil {
		return e
	}
	if b.db.Meeting(mid) == nil {
		return errors.New("会议不存在")
	}
	if e := b.settings.save(M{"selected": mid}); e != nil {
		return e
	}
	b.detach()
	b.stopScreen()
	b.mid = mid
	b.limit = 120
	b.files = nil
	b.asr.ForgetSpeakers()
	b.setFrameRequest()
	b.changed()
	b.event("workspace", "")
	return nil
}
func (b *bridge) asrConfig() transcription.Config {
	p := str(b.settings.values["provider"])
	return transcription.Config{Provider: p, Model: str(b.settings.values[p+"_model"]), APIKey: b.settings.key(p)}
}
func (b *bridge) start() error {
	if e := b.asr.EnsureIdle(); e != nil {
		return e
	}
	m := b.current()
	if truth(m["demo"]) {
		return errors.New("请新建会议后开始采集")
	}
	s, e := b.source(str(m["source"]))
	if e != nil {
		return e
	}
	if s.Kind != "file" && s.Label != str(m["source_label"]) {
		return errors.New("采集源已变化，请重新选择")
	}
	at := number(m["duration"])
	return b.asr.Record(b.mid, s, b.asrConfig(), number(b.settings.values["chunk"]), at)
}
func (b *bridge) export(target string, m M) error {
	var out strings.Builder
	fmt.Fprintf(&out, "# %s\n\n%s\n\n## 会议动态\n\n", m["title"], m["created"])
	mid := str(m["id"])
	for _, e := range b.db.Events(mid, 0, 0) {
		t := int(number(e["at"]))
		stamp := "未标时"
		if truth(e["timed"]) {
			stamp = fmt.Sprintf("%02d:%02d:%02d", t/3600, t/60%60, t%60)
		}
		fmt.Fprintf(&out, "**%s · %s**\n\n%s\n\n", stamp, e["speaker"], e["text"])
		if f := str(e["files"]); f != "" && f != "[]" {
			fmt.Fprintf(&out, "附件：%s\n\n", f)
		}
	}
	kinds := map[string]string{"question": "待解问题", "task": "待办事项", "idea": "灵感想法", "decision": "会议决策"}
	out.WriteString("## 会议便签\n\n")
	for _, n := range b.db.Notes(mid) {
		fmt.Fprintf(&out, "### %s\n\n%s\n\n%s\n\n", n["title"], kinds[str(n["kind"])], n["body"])
	}
	return os.WriteFile(target, []byte(out.String()), 0600)
}
func (b *bridge) setFrameRequest() {
	if !truth(b.settings.values["share_frames"]) {
		b.frameRequest.Store(nil)
		return
	}
	b.frameRequest.Store(&frameRequest{b.mid, b.live, b.screenVersion})
}
func (b *bridge) turnFrame(mid string, at float64) string {
	r := b.frameRequest.Load()
	if r == nil || r.meeting != mid {
		return ""
	}
	ctx, cancel := context.WithTimeout(b.ctx, 14*time.Second)
	defer cancel()
	if r.live == nil {
		return ""
	}
	path, err := r.live.Snapshot(ctx)
	if err != nil || b.frameRequest.Load() != r {
		return ""
	}
	return path
}
func (b *bridge) gotFrame(version int, path string) {
	go func() {
		preview := media.Thumbnail(b.ctx, path, 480)
		b.post(func() {
			if b.screenVersion == version {
				b.frame = path
				b.preview = preview
				b.changed()
			}
		})
	}()
}
func (b *bridge) stopScreen() {
	b.screenVersion++
	b.windows = nil
	if b.live != nil {
		b.live.Stop()
		b.live = nil
	}
	b.frame = ""
	b.preview = ""
	b.frameRequest.Store(nil)
}
