package desktop

import (
	"errors"
	"fmt"
	"github.com/zzttzzmyswy/tingsu/internal/screen"
	"math"
	"path/filepath"
	"reflect"
	"runtime"
	"slices"
	"strings"
	"unicode"
)

func (b *bridge) command(name string, a M) error {
	defer b.changed()
	switch name {
	case "sources":
		b.loadSources()
	case "import_media":
		b.dialog("open-files", "添加会议录屏或音频", M{}, func(v any) {
			if files := stringsOf(v); len(files) > 0 {
				if e := b.db.AddMedia(files); e != nil {
					b.fail(e)
					return
				}
				b.loadLibrary()
			}
		})
	case "settings":
		return b.saveSettings(a)
	case "select":
		if id := str(a["id"]); id != b.mid {
			return b.change(id)
		}
	case "new":
		if e := b.canSwitch(); e != nil {
			return e
		}
		title := trimTitle(str(a["title"]))
		if title == "" {
			title = "未命名会议"
		}
		id := str(a["source"])
		label := ""
		if id != "" {
			s, e := b.source(id)
			if e != nil {
				return e
			}
			label = s.Label
		}
		offset := number(a["offset"])
		if offset < 0 || math.IsNaN(offset) || math.IsInf(offset, 0) || (offset > 0 && offset >= number(b.findMedia(id)["duration"])) {
			return errors.New("录屏起点必须在媒体时长之内")
		}
		mid, e := b.db.Create(title, id, label, offset, str(a["topic_id"]), str(a["topic_name"]))
		if e != nil {
			return e
		}
		if e = b.change(mid); e != nil {
			return e
		}
		if truth(a["start"]) {
			return b.start()
		}
	case "rename":
		title := trimTitle(str(a["title"]))
		if title == "" {
			return errors.New("请输入会议名称")
		}
		return b.db.RenameMeeting(b.mid, title)
	case "set_meeting_topic":
		if str(a["meeting"]) != b.mid {
			return errors.New("会议已切换，请重新选择主题")
		}
		if e := b.canSwitch(); e != nil {
			return e
		}
		if e := b.db.SetMeetingTopic(b.mid, str(a["topic_id"]), str(a["topic_name"])); e != nil {
			return e
		}
		b.detach()
		b.files = nil
		b.publish()
		b.event("topic-saved", "")
		b.notice("会议已加入主题", "ok")
	case "set_source":
		if e := b.asr.EnsureIdle(); e != nil {
			return e
		}
		s, e := b.source(str(a["id"]))
		if e != nil {
			return e
		}
		m := b.current()
		origin, end := number(m["origin"]), number(m["duration"])
		if truth(m["demo"]) || (end > origin && (filepath.IsAbs(str(m["source"])) || s.Kind == "file")) {
			return errors.New("已有录音的会议不能更换录屏文件。请新建会议选择文件。")
		}
		if sourceKey(s) != str(m["source"]) && end <= origin {
			origin, end = 0, 0
		}
		if e = b.db.SetSource(b.mid, sourceKey(s), s.Label, origin, end); e != nil {
			return e
		}
		b.setFrameRequest()
		if truth(a["start"]) {
			return b.start()
		}
	case "older":
		b.limit += 160
	case "jump":
		at := number(a["at"])
		b.limit = max(b.limit, b.db.CountEventsFrom(b.mid, at)+2)
		b.publish()
		b.event("jump", fmt.Sprint(at))
	case "search_events":
		mid := b.mid
		if str(a["meeting"]) != mid {
			return nil
		}
		offset := max(0, int(number(a["offset"])))
		go func() {
			total, items, e := b.db.SearchEvents(mid, strings.TrimSpace(str(a["query"])), str(a["speaker"]), str(a["filter"]), offset)
			b.post(func() {
				if b.mid == mid {
					if e != nil {
						b.fail(e)
						return
					}
					b.event("search-results", encoded(M{"meeting": mid, "id": a["id"], "offset": offset, "total": total, "items": items}))
				}
			})
		}()
	case "note", "topic_note":
		ops := map[string]string{"add": "create_note", "edit": "update_note", "delete": "delete_note"}
		if name == "topic_note" {
			if str(a["meeting"]) != b.mid {
				return errors.New("会议已切换，请重新打开主题看板。")
			}
			ops = map[string]string{"add": "create_topic_note", "edit": "update_topic_note", "delete": "delete_topic_note", "status": "set_topic_note_status", "share": "share_note_to_topic"}
		}
		op := ops[str(a["op"])]
		if op == "" {
			return errors.New("未知便签操作")
		}
		if _, e := b.db.Tool(b.mid, op, a, "我"); e != nil {
			return e
		}
		b.publish()
		b.event("note-saved", "")
	case "export":
		m := b.current()
		title := strings.Map(func(r rune) rune {
			if unicode.IsLetter(r) || unicode.IsNumber(r) || strings.ContainsRune(" -_", r) {
				return r
			}
			return '_'
		}, str(m["title"]))
		b.dialog("save-file", "导出会议记录", M{"path": title + ".md", "filter": "*.md"}, func(v any) {
			if target := str(v); target != "" {
				if e := b.export(target, m); e != nil {
					b.fail(e)
				} else {
					b.notice("会议记录已导出", "ok")
				}
			}
		})
	case "start":
		return b.start()
	case "stop":
		b.asr.Stop()
	case "retry":
		return b.asr.Retry(b.mid, b.asrConfig())
	case "attach":
		if b.status != "ready" {
			return errors.New("请先连接 AI 会话")
		}
		g := b.generation
		b.dialog("open-files", "添加会议材料", M{}, func(v any) {
			if b.generation == g {
				for _, s := range stringsOf(v) {
					if !slices.Contains(b.files, s) {
						b.files = append(b.files, s)
					}
				}
				b.changed()
			}
		})
	case "remove_file":
		i := int(number(a["index"]))
		if i >= 0 && i < len(b.files) {
			b.files = slices.Delete(b.files, i, i+1)
		}
	case "screen_off":
		b.stopScreen()
		b.setFrameRequest()
	case "screen_windows":
		b.listWindows()
	case "screen", "snapshot":
		if b.live != nil {
			s := b.live
			v := b.screenVersion
			go func() {
				p, e := s.Snapshot(b.ctx)
				if e != nil {
					b.post(func() {
						if b.screenVersion == v {
							b.fail(e)
						}
					})
				} else {
					b.gotFrame(v, p)
				}
			}()
			return nil
		}
		src := str(b.current()["source"])
		if name == "snapshot" && filepath.IsAbs(src) {
			v, mid := b.screenVersion, b.mid
			t, ok := b.asr.Position()
			if !ok {
				t = number(b.current()["duration"])
			}
			go func() {
				p, e := screen.FileFrame(b.ctx, src, t, filepath.Join(b.data, "frames", mid))
				if e != nil {
					b.post(func() {
						if b.screenVersion == v {
							b.fail(e)
						}
					})
				} else {
					b.gotFrame(v, p)
				}
			}()
			return nil
		}
		if runtime.GOOS == "windows" && a["window"] == nil {
			b.listWindows()
			return nil
		}
		var choice *screen.Window
		if id := str(a["window"]); id != "" {
			w, ok := b.windows[id]
			if !ok {
				return errors.New("请重新选择共享窗口")
			}
			choice = &w
		}
		b.screenVersion++
		v := b.screenVersion
		b.windows = nil
		s, e := screen.Start(b.ctx, filepath.Join(b.data, "frames", b.mid), choice, func(p string) { b.gotFrame(v, p) }, func(e error) {
			b.post(func() {
				if b.screenVersion == v {
					b.stopScreen()
					b.setFrameRequest()
					b.fail(e)
				}
			})
		})
		if e != nil {
			return e
		}
		b.live = s
		b.setFrameRequest()
	case "frame_attach":
		if b.frame != "" && !slices.Contains(b.files, b.frame) {
			b.files = append(b.files, b.frame)
		}
	case "connect":
		return b.connect(a)
	case "session":
		return b.openSession(a)
	case "disconnect":
		b.detach()
	case "send":
		if e := b.turns.Submit(str(a["text"]), slices.Clone(b.files)); e != nil {
			return e
		}
		b.files = nil
		b.event("sent", "")
	case "cancel_agent":
		b.turns.Cancel()
	case "open_agent_workspace":
		if b.status != "ready" || b.provider == "openai" {
			return errors.New("请先连接本机 AI 助手")
		}
		return openExternal(b.client.Workspace())
	case "login":
		return b.login()
	case "login_browser":
		if b.loginURL == "" {
			return errors.New("尚未获得登录链接")
		}
		return openExternal(b.loginURL)
	case "codex_models":
		b.loadCatalog(str(a["path"]))
	default:
		return fmt.Errorf("未知操作 %s", name)
	}
	return nil
}
func trimTitle(s string) string {
	r := []rune(strings.TrimSpace(s))
	return string(r[:min(len(r), 120)])
}
func (b *bridge) listWindows() {
	v := b.screenVersion
	go func() {
		windows, e := screen.Windows(b.ctx)
		b.post(func() {
			if v != b.screenVersion {
				return
			}
			if e != nil {
				b.fail(e)
				return
			}
			b.windows = map[string]screen.Window{}
			for _, w := range windows {
				b.windows[w.ID] = w
			}
			if windows == nil {
				windows = []screen.Window{}
			}
			b.event("screen-windows", encoded(windows))
		})
	}()
}
func openExternal(target string) error {
	c := openCmd(target)
	background(c)
	if e := c.Start(); e != nil {
		return e
	}
	go c.Wait()
	return nil
}
func (b *bridge) saveSettings(a M) error {
	values := M{}
	allowed := defaults()
	delete(allowed, "selected")
	for k := range a {
		if _, ok := allowed[k]; ok || slices.Contains([]string{"siliconflow_key", "sophnet_key", "openai_key"}, k) {
			values[k] = a[k]
		}
	}
	if p, ok := values["provider"]; ok && p != "siliconflow" && p != "sophnet" {
		return errors.New("未知转写供应商")
	}
	if p, ok := values["agent"]; ok && providers[str(p)] == nil {
		return errors.New("未知 AI 服务")
	}
	if c, ok := values["chunk"]; ok {
		values["chunk"] = min(45, max(4, int(number(c))))
	}
	saved, _ := b.settings.values["local_agent_options"].(M)
	if raw, ok := values["local_agent_options"]; ok {
		updated, ok := raw.(M)
		if !ok {
			return errors.New("无效的 AI 设置")
		}
		next := M{}
		for p, v := range saved {
			next[p] = v
		}
		for p, v := range updated {
			if p == "openai" || providers[p] == nil {
				return errors.New("未知本机 AI 助手")
			}
			v, e := prefs(v)
			if e != nil {
				return e
			}
			next[p] = v
		}
		values["local_agent_options"] = next
	}
	changed := false
	if b.client != nil {
		for _, k := range []string{"agent", "model", "openai_key", b.provider + "_path"} {
			if v, ok := values[k]; ok && !reflect.DeepEqual(v, b.settings.values[k]) {
				changed = true
			}
		}
		if opts, ok := values["local_agent_options"].(M); ok {
			old, _ := prefs(saved[b.provider])
			next, _ := prefs(opts[b.provider])
			changed = changed || !reflect.DeepEqual(old, next)
		}
	}
	if changed {
		if s, q := b.turns.State(); s || q > 0 {
			return errors.New("请先结束当前 AI 回复，再更改设置。")
		}
	}
	if e := b.settings.save(values); e != nil {
		return e
	}
	if changed {
		b.detach()
	}
	b.turns.SetAutomatic(truth(b.settings.values["auto"]))
	b.setFrameRequest()
	b.publish()
	b.event("settings-saved", "")
	b.notice("设置已保存", "ok")
	return nil
}
