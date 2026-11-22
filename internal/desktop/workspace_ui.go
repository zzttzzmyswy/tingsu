package desktop

import (
	"fmt"
	"strconv"
	"strings"

	"gioui.org/io/key"
	"gioui.org/layout"
	"gioui.org/widget"
	"gioui.org/widget/material"
)

type choice struct{ id, label string }

var kinds = []choice{{"question", "问题"}, {"task", "待办"}, {"decision", "决策"}, {"idea", "想法"}}
var statuses = []choice{{"open", "待跟进"}, {"in_progress", "进行中"}, {"done", "已完成"}}
var services = []choice{{"codex", "Codex"}, {"claude", "Claude Code"}, {"deepseek", "DeepSeek Harness"}}

func labelOf(opts []choice, id string) string {
	for _, c := range opts {
		if c.id == id {
			return c.label
		}
	}
	return id
}
func (u *workbench) shortcuts(g C) {
	mod := key.ModShortcut
	if u.modal == "" {
		menus := []string{"menu/file", "menu/meeting", "menu/view", "menu/settings"}
		for {
			ev, ok := g.Event(key.Filter{Name: "F", Required: key.ModAlt}, key.Filter{Name: "M", Required: key.ModAlt}, key.Filter{Name: "V", Required: key.ModAlt}, key.Filter{Name: "S", Required: key.ModAlt}, key.Filter{Name: "F10"})
			if !ok {
				break
			}
			if k, ok := ev.(key.Event); ok && k.State == key.Press {
				index := map[key.Name]int{"M": 1, "V": 2, "S": 3}[k.Name]
				u.showPopup(menus[index], false)
			}
		}
		for i, id := range menus {
			if u.popup.id != id {
				continue
			}
			if ev, ok := g.Event(key.Filter{Name: key.NameLeftArrow}, key.Filter{Name: key.NameRightArrow}); ok {
				if k, ok := ev.(key.Event); ok && k.State == key.Press {
					step := 1
					if k.Name == key.NameLeftArrow {
						step = -1
					}
					u.showPopup(menus[(i+len(menus)+step)%len(menus)], false)
				}
			}
			break
		}
	}
	if u.popup.id != "" {
		for {
			ev, ok := g.Event(key.Filter{Name: key.NameEscape})
			if !ok {
				break
			}
			if k, ok := ev.(key.Event); ok && k.State == key.Press {
				u.closePopup()
			}
		}
		return
	}
	for {
		ev, ok := g.Event(key.Filter{Name: "L", Required: mod}, key.Filter{Name: "R", Required: mod}, key.Filter{Name: "G", Required: mod}, key.Filter{Name: "I", Required: mod}, key.Filter{Name: "J", Required: mod}, key.Filter{Name: ",", Required: mod}, key.Filter{Name: "N", Required: mod}, key.Filter{Name: "K", Required: mod}, key.Filter{Name: "F", Required: mod}, key.Filter{Name: "S", Required: mod}, key.Filter{Name: key.NameReturn, Required: mod}, key.Filter{Name: key.NameEscape})
		if !ok {
			break
		}
		e, ok := ev.(key.Event)
		if !ok || e.State != key.Press {
			continue
		}
		if e.Name == key.NameEscape {
			u.back()
			continue
		}
		if u.modal == "" {
			switch e.Name {
			case "L":
				u.openAudio()
			case "R":
				u.toggleRecording()
			case "G":
				u.open("screen")
				u.cmd("screen", nil)
			case "I":
				u.importTranscript()
			case "J":
				u.open("screen")
				u.cmd("snapshot", nil)
			case ",":
				u.settingsForm()
			case "N":
				u.newMeeting()
			case "K":
				u.editNote(nil)
			case "F":
				u.openSearch()
			case key.NameReturn:
				u.finishEditing(g)
				u.sendMessage()
			}
			continue
		}
		if e.Name == "S" || e.Name == key.NameReturn {
			u.finishEditing(g)
			u.submitForm()
		}
	}
}

// Text input and a save shortcut may arrive in the same frame. Apply pending
// edits before reading the form or chat draft.
func (u *workbench) finishEditing(g C) {
	for _, ed := range u.editors {
		if g.Focused(ed) {
			for {
				if _, ok := ed.Update(g); !ok {
					break
				}
			}
			return
		}
	}
}

func (u *workbench) back() {
	switch u.modal {
	case "message", "path":
		u.dialogResult(false)
	case "confirm":
		u.open("")
		u.confirm = nil
	default:
		u.open("")
	}
}
func (u *workbench) pane(g C, content W) D {
	g.Constraints.Min = g.Constraints.Max
	return padding{Top: 8, Bottom: 8}.Wrap(func(g C) D { return box(g, paper, 14, inset(14, content)) })(g)
}
func (u *workbench) feed(g C) D {
	return u.pane(g, func(g C) D {
		return (layout.Flex{Axis: layout.Vertical}).Layout(g,
			layout.Rigid(func(g C) D {
				filter := []choice{{"", "全部记录"}, {"speech", "会议发言"}, {"assistant", "AI 回复"}, {"user", "我的消息"}}
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, col(3, u.text(str(u.meeting()["title"]), 22, ink, true), u.oneLine("会议记录", 13, muted))), layout.Rigid(fixedWidth(105, u.drop("feed/filter", labelOf(filter, u.feedKind), filter, u.feedKind, false, func(v string) { u.feedKind = v; u.records = recordView{}; u.updateFeed() }))), layout.Rigid(layout.Spacer{Width: 6}.Layout), layout.Rigid(u.button("find", "查找", false, u.openSearch)))
			}),
			layout.Flexed(1, u.recordList),
			layout.Rigid(u.composer))
	})
}
func kindLabel(s string) string {
	switch s {
	case "speech":
		return "发言"
	case "assistant":
		return "AI 回复"
	case "user":
		return "我的消息"
	case "system":
		return "系统"
	}
	return s
}
func (u *workbench) eventRow(g C, e M) D {
	id := fmt.Sprint(e["seq"])
	kind := str(e["kind"])
	who := str(e["speaker"])
	if who == "" {
		who = kindLabel(kind)
	}
	style := material.Label(u.th, u.fontSize(15), str(e["text"]))
	style.Color = ink
	if u.selectable[id] == nil {
		u.selectable[id] = new(widget.Selectable)
	}
	style.State = u.selectable[id]
	stamp := clockText(number(e["at"]))
	if known, ok := e["timed"]; ok && !truth(known) {
		stamp = "未标时"
	}
	header := u.text(stamp+"   "+who, 13, ink, true)
	if kind == "speech" {
		tag := stamp + "   " + who
		quote := tag + "\n" + str(e["text"])
		header = func(g C) D {
			return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text(tag, 13, ink, true)), layout.Rigid(row(4, u.button("quote"+id, "引用", false, func() { u.appendDraft(quote) }), u.button("pin"+id, "建便签", false, func() { u.editNote(nil); u.edit("form/body").SetText(quote) }))))
		}
	}
	body := W(style.Layout)
	if kind == "assistant" {
		body = u.markdownBody(id, str(e["text"]), style.Layout)
		sourceLabel := "原文"
		if u.markdownRows[id].showSource {
			sourceLabel = "排版"
		}
		header = func(g C) D {
			return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text(stamp+"   "+who, 13, accentInk, true)), layout.Rigid(row(4, u.button("source/"+id, sourceLabel, false, func() {
				r := u.markdownRows[id]
				r.showSource = !r.showSource
				u.markdownRows[id] = r
			}), u.button("copy/"+id, "复制", false, func() { copyText(g, str(e["text"])) }))))
		}
	}
	content := []W{header, body}
	if files := attachmentNames(e); files != "" {
		content = append(content, u.text("附件："+files, 12, muted, false))
	}
	bg := paper
	if kind == "assistant" {
		bg = mix(paper, selection, 9)
	} else if kind == "user" {
		bg = canvas
	}
	return layout.Inset{Top: 3, Bottom: 3, Right: 7}.Layout(g, func(g C) D { return box(full(g), bg, 12, inset(12, col(5, content...))) })
}
func (u *workbench) appendDraft(quote string) {
	ed := u.edit("message")
	ed.SetText(strings.TrimSpace(ed.Text() + "\n" + quote))
	u.nextFocus = ed
}
func (u *workbench) sendMessage() {
	s := u.edit("message").Text()
	if strings.TrimSpace(s) == "" && len(stringsOf(u.state["files"])) == 0 {
		return
	}
	u.pendingSend = s
	u.cmd("send", M{"text": s})
}
func (u *workbench) composer(g C) D {
	var controls []W
	for i, f := range stringsOf(u.state["files"]) {
		controls = append(controls, u.button(fmt.Sprint("file", i), f+" ×", false, func() { u.cmd("remove_file", M{"index": i}) }))
	}
	controls = append(controls, u.field("message", "向 AI 提问或补充会议内容…", true, false))
	actions := []W{u.button("attach", "附件", false, func() { u.cmd("attach", nil) })}
	if truth(u.state["sending"]) {
		actions = append(actions, u.button("stop-reply", "停止回复", false, func() { u.cmd("cancel_agent", nil) }))
	}
	if int(number(u.state["queued"])) > 0 {
		actions = append(actions, u.text(fmt.Sprintf("排队 %d", int(number(u.state["queued"]))), 12, muted, false))
	}
	controls = append(controls, func(g C) D {
		return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, row(4, actions...)), layout.Rigid(u.button("send", "发送", true, u.sendMessage)))
	})
	return col(5, controls...)(g)
}
func (u *workbench) board(g C) D {
	return u.pane(g, func(g C) D {
		var filters []W
		grouped := str(u.meeting()["topic_id"]) != ""
		if grouped {
			filters = append(filters, u.drop("board/scope", labelOf(boardScopes, u.boardScope), boardScopes, u.boardScope, false, func(v string) { u.boardScope = v }))
		}
		options := append([]choice{{"", "全部类型"}}, kinds...)
		kindFilter := fixedWidth(105, u.drop("board/kind", labelOf(options, u.boardKind), options, u.boardKind, false, func(v string) { u.boardKind = v }))
		statusOptions := append([]choice{{"pending", "未完成"}, {"", "全部状态"}}, statuses...)
		filters = append(filters, row(4, kindFilter, func(g C) D {
			if !grouped || u.boardScope != "topic" {
				return D{}
			}
			return u.drop("board/status", labelOf(statusOptions, u.boardStatus), statusOptions, u.boardStatus, false, func(v string) { u.boardStatus = v })(g)
		}))
		var cards []W
		for _, n := range boardCards(rows(u.state["notes"]), rows(u.state["topicNotes"]), u.boardScope, u.boardKind, u.boardStatus) {
			kind, status := str(n["kind"]), str(n["status"])
			cards = append(cards, func(g C) D {
				id := str(n["id"])
				btn := u.click("card" + id)
				for btn.Clicked(g) {
					u.editNote(n)
				}
				bg := paper
				if btn.Hovered() || btn.Pressed() {
					bg = hover
				}
				meta := labelOf(kinds, kind)
				if grouped {
					meta += " · " + labelOf(noteScopes, noteScope(n))
				}
				if status != "" {
					meta += "   ·   " + labelOf(statuses, status)
				}
				content := []W{func(g C) D {
					return layout.W.Layout(g, func(g C) D {
						return box(g, mix(bg, selection, 13), 6, padding{Left: 6, Right: 6, Top: 2, Bottom: 2}.Wrap(u.text(meta, 12, accentInk, true)))
					})
				}, u.text(str(n["title"]), 17, ink, true)}
				if s := str(n["body"]); s != "" {
					content = append(content, u.text(s, 14, ink, false))
				}
				if s := str(n["source_title"]); s != "" {
					content = append(content, u.text("来自 "+s, 12, muted, false))
				}
				return btn.Layout(g, func(g C) D { return card(full(g), bg, 12, inset(12, col(6, content...))) })
			})
		}
		if len(cards) == 0 {
			cards = append(cards, u.text("暂无符合条件的便签", 14, muted, false))
		}
		return (layout.Flex{Axis: layout.Vertical}).Layout(g,
			layout.Rigid(func(g C) D {
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text("便签看板", 19, ink, true)), layout.Rigid(u.button("board-export", "导出", false, func() { u.exportForm("board") })), layout.Rigid(layout.Spacer{Width: 4}.Layout), layout.Rigid(u.button("add-note", "＋ 新建", true, func() { u.editNote(nil) })))
			}),
			layout.Rigid(padding{Top: 8, Bottom: 8}.Wrap(col(5, filters...))), layout.Flexed(1, func(g C) D { return u.textPane(g, &u.boardText, func(g C) D { return u.scroll(g, "board", cards) }) }))
	})
}
func (u *workbench) jump(at float64) {
	u.open("")
	u.feedKind = ""
	u.updateFeed()
	for i, e := range u.records.rows {
		if e != nil && number(e["at"]) >= at {
			u.records.bar = thumbDrag{}
			u.records.list.ScrollTo(i)
			return
		}
	}
}
func (u *workbench) openSearch() {
	u.results = nil
	u.form("search", map[string]string{"query": "", "speaker": "", "filter": ""}, "query")
}
func (u *workbench) search(offset int) {
	u.querySerial++
	u.cmd("search_events", M{"meeting": u.meeting()["id"], "id": u.querySerial, "query": u.val("query"), "speaker": u.fields["speaker"], "filter": u.fields["filter"], "offset": offset})
}
func (u *workbench) newMeeting() {
	u.cmd("sources", nil)
	u.form("new", map[string]string{"title": "", "topic_id": "", "topic_name": "", "source": ""}, "title")
}
func (u *workbench) importTranscript() { u.cmd("import_transcript", nil) }
func (u *workbench) editNote(n M) {
	u.note = n
	kind := str(n["kind"])
	if kind == "" {
		kind = u.boardKind
		if kind == "" {
			kind = "question"
		}
	}
	status := str(n["status"])
	if status == "" {
		status = "open"
	}
	scope := noteScope(n)
	if n == nil && u.boardScope == "topic" && str(u.meeting()["topic_id"]) != "" {
		scope = "topic"
	}
	u.form("note", map[string]string{"scope": scope, "kind": kind, "status": status, "title": str(n["title"]), "body": str(n["body"])}, "title")
}
func (u *workbench) noteCommand(op string) {
	k := "note"
	if u.fields["scope"] == "topic" || op == "share" {
		k = "topic_note"
	}
	a := M{"op": op, "meeting": u.meeting()["id"], "id": u.note["id"], "version": u.note["version"], "kind": u.fields["kind"], "status": u.fields["status"], "title": u.val("title"), "body": u.val("body"), "editor": "native-note"}
	u.cmd(k, a)
}
func (u *workbench) dialogResult(yes bool) {
	m := u.dlg
	u.dlg = nil
	u.open("")
	u.send(packet{Type: "dialog-result", ID: int(number(m["id"])), Value: yes})
}
func (u *workbench) saveNew(start bool) {
	topicName := u.val("topic_name")
	if u.fields["topic_id"] != "" {
		topicName = ""
	}
	u.cmd("new", M{"title": u.val("title"), "topic_id": u.fields["topic_id"], "topic_name": topicName, "source": u.fields["source"], "start": start})
}
func (u *workbench) submitForm() {
	switch u.modal {
	case "new":
		u.saveNew(false)
	case "note":
		op := "add"
		if u.note != nil {
			op = "edit"
		}
		u.noteCommand(op)
	case "settings":
		u.savePrefs()
	case "rename":
		u.cmd("rename", M{"title": u.val("title")})
		u.open("")
	case "topic":
		u.cmd("set_meeting_topic", M{"meeting": u.meeting()["id"], "topic_id": u.fields["topic_id"], "topic_name": u.val("topic_name")})
	case "windows":
		q := strings.ToLower(u.edit("window-search").Text())
		for _, w := range u.windows {
			if strings.Contains(strings.ToLower(str(w["title"])), q) {
				u.cmd("screen", M{"window": w["id"]})
				u.open("screen")
				break
			}
		}
	case "export":
		u.cmd("export", M{"meeting": u.fields["meeting"], "section": u.fields["section"], "scope": u.fields["scope"], "format": u.fields["format"]})
		u.open("")
	case "path":
		u.acceptPath()
	case "search":
		u.search(0)

	}
}
func (u *workbench) settingsForm() {
	s := obj(u.state["settings"])
	v := map[string]string{}
	for _, k := range []string{"provider", "agent", "siliconflow_model", "sophnet_model", "codex_path", "claude_path", "deepseek_path"} {
		v[k] = str(s[k])
	}
	for _, k := range []string{"auto", "share_frames"} {
		v[k] = strconv.FormatBool(truth(s[k]))
	}
	v["chunk"] = fmt.Sprint(s["chunk"])
	for _, p := range services {
		v[p.id+"_key"] = ""
		for _, k := range []string{"model", "effort", "speed"} {
			v[p.id+"."+k] = str(obj(obj(s["local_agent_options"])[p.id])[k])
		}
	}
	v["sophnet_key"] = ""
	v["siliconflow_key"] = ""
	u.prefsTab = "asr"
	u.localCLI = str(s["agent"])
	if providers[u.localCLI] == nil {
		u.localCLI = "codex"
	}
	u.custom = map[string]bool{}
	u.form("settings", v, "")
}
func (u *workbench) savePrefs() {
	a := M{}
	for _, k := range []string{"provider", "agent"} {
		a[k] = u.fields[k]
	}
	for _, k := range []string{"siliconflow_model", "sophnet_model", "codex_path", "claude_path", "deepseek_path"} {
		a[k] = u.val(k)
	}
	for _, k := range []string{"auto", "share_frames"} {
		a[k] = u.fields[k] == "true"
	}
	chunk, err := strconv.Atoi(u.fields["chunk"])
	if err != nil || chunk < 4 || chunk > 45 {
		u.toast("分段长度须为 4–45 秒", "error")
		return
	}
	a["chunk"] = chunk
	for _, k := range []string{"siliconflow_key", "sophnet_key"} {
		if v := u.val(k); v != "" {
			a[k] = v
		}
	}
	opts := M{}
	for _, p := range []string{"codex", "claude", "deepseek"} {
		o := M{}
		for _, k := range []string{"model", "speed", "effort"} {
			o[k] = u.val(p + "." + k)
		}
		opts[p] = o
	}
	a["local_agent_options"] = opts
	u.cmd("settings", a)
}
