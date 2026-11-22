package desktop

import (
	"fmt"
	"image"
	"strings"

	"gioui.org/layout"
	"gioui.org/unit"
	"gioui.org/widget"
)

var formTitles = map[string]string{"history": "会议与主题", "new": "新建会议", "note": "编辑便签", "settings": "首选项", "agent": "助手会话", "screen": "会议画面", "windows": "选择会议窗口", "topic": "加入主题", "rename": "重命名会议", "search": "查找本场会议记录", "confirm": "确认操作", "speakers": "发言人", "path": "选择文件"}

func (u *workbench) modalLayout(g C) D {
	title := formTitles[u.modal]
	if u.modal == "export" {
		title = "导出" + exportLabel(u.fields["section"])
	}
	if u.modal == "message" {
		title = str(u.dlg["title"])
	}
	if u.modal == "note" && u.note == nil {
		title = "新建便签"
	}
	width, height := 580, 350
	switch u.modal {
	case "settings":
		width, height = 800, 495
	case "screen", "search", "history", "windows":
		width, height = 710, 520
	case "new", "note":
		height = 450
	case "message", "rename", "confirm":
		height = 230
	}
	return layout.Center.Layout(g, func(g C) D {
		w, h := min(g.Constraints.Max.X-g.Dp(30), g.Dp(unit.Dp(width))), min(g.Constraints.Max.Y-g.Dp(36), g.Dp(unit.Dp(height)))
		g.Constraints = layout.Exact(image.Pt(w, h))
		return box(g, paper, 6, func(g C) D {
			return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Rigid(padding{Top: 9, Bottom: 9, Left: 16, Right: 12}.Wrap(func(g C) D {
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text(title, 18, ink, true)), layout.Rigid(u.button("back", "关闭", false, u.back)))
			})), layout.Rigid(rule), layout.Flexed(1, func(g C) D {
				if u.modal == "settings" {
					return u.prefsLayout(g)
				}
				return inset(15, func(g C) D { return u.scroll(g, "form", u.formContent()) })(g)
			}))
		})
	})
}
func (u *workbench) pick(k, title string, opts []choice) W {
	return col(4, u.text(title, 13, muted, false), u.drop("form/"+k, labelOf(opts, u.fields[k]), opts, u.fields[k], len(opts) > 8, func(v string) {
		u.fields[k] = v
		if k == "topic_id" && v != "" {
			u.edit("form/topic_name").SetText("")
		}
	}))
}
func (u *workbench) topicChoices() []choice {
	out := []choice{{"", "独立会议或填写新主题"}}
	for _, m := range rows(u.state["topics"]) {
		out = append(out, choice{str(m["id"]), str(m["name"])})
	}
	return out
}
func (u *workbench) sourceChoices() []choice {
	out := []choice{{"", "暂不采集"}}
	for _, suffix := range []string{"-monitor", "-mic", "-app"} {
		for _, m := range rows(u.state["sources"]) {
			if strings.HasSuffix(str(m["kind"]), suffix) {
				out = append(out, choice{str(m["id"]), str(m["label"])})
			}
		}
	}
	return out
}
func (u *workbench) formContent() []W {
	var ws []W
	add := func(w W) { ws = append(ws, w) }
	action := func(id, title string, p bool, fn func()) { add(u.button(u.modal+"/"+id, title, p, fn)) }
	edit := func(k, title string, multi, secret bool) {
		add(col(4, u.text(title, 13, muted, true), u.field("form/"+k, title, multi, secret)))
	}
	open := func(id, title string, fn func()) { action(id, title, false, fn) }
	submit := func(title string) { action("save", title, true, u.submitForm) }
	switch u.modal {
	case "export":
		if u.fields["section"] == "board" && str(u.meeting()["topic_id"]) != "" {
			add(u.pick("scope", "便签范围", boardScopes))
		}
		add(u.pick("format", "文件格式", exportFormats))
		submit("选择保存位置…")
	case "history":
		add(u.field("history-find", "查找会议或主题", false, false))
		q := strings.ToLower(u.edit("history-find").Text())
		for _, m := range rows(u.state["meetings"]) {
			t := str(m["title"])
			if topic := str(m["topic_name"]); topic != "" {
				t += "   ·   " + topic
			}
			if !strings.Contains(strings.ToLower(t), q) {
				continue
			}
			id := str(m["id"])
			action(id, t, id == str(u.meeting()["id"]), func() { u.cmd("select", M{"id": id}); u.open("") })
		}
	case "new", "topic", "rename":
		if u.modal != "topic" {
			edit("title", "会议名称", false, false)
		}
		if u.modal != "rename" {
			add(u.pick("topic_id", "会议主题", u.topicChoices()))
			if u.fields["topic_id"] == "" {
				edit("topic_name", "新主题名称（可选）", false, false)
			}
		}
		if u.modal == "new" {
			add(u.pick("source", "收音来源", u.sourceChoices()))
			submit("创建会议")
			if u.fields["source"] != "" {
				action("start", "创建并开始转写", false, func() { u.saveNew(true) })
			}
		} else {
			submit("保存")
		}
	case "note":
		edit("title", "标题", false, false)
		edit("body", "内容", true, false)
		add(u.pick("kind", "类型", kinds))
		if u.note == nil && str(u.meeting()["topic_id"]) != "" {
			add(u.pick("scope", "保存范围", noteScopes))
		}
		if u.fields["scope"] == "topic" {
			add(u.pick("status", "状态", statuses))
		}
		submit("保存便签")
		if u.note != nil {
			if u.fields["scope"] != "topic" && str(u.meeting()["topic_id"]) != "" {
				open("share", "共享到主题", func() { u.noteCommand("share") })
			}
			open("ask", "引用到 AI 输入框", func() {
				ed := u.edit("message")
				ed.SetText(strings.TrimSpace(ed.Text() + "\n" + u.val("title") + "\n" + u.val("body")))
				u.open("")
			})
			open("del", "删除便签", func() {
				u.confirm = func() { u.noteCommand("delete") }
				u.choiceTitle = "删除「" + str(u.note["title"]) + "」？"
				u.open("confirm")
			})
		}
	case "confirm":
		add(u.text(u.choiceTitle, 17, ink, true))
		action("yes", "确认", true, func() {
			fn := u.confirm
			u.confirm = nil
			u.open("")
			if fn != nil {
				fn()
			}
		})
	case "message":
		add(u.text(str(u.dlg["message"]), 16, ink, false))
		action("yes", "确定", true, func() { u.dialogResult(true) })
		if u.dlg["kind"] == "confirm" {
			open("no", "取消", func() { u.dialogResult(false) })
		}
	case "agent":
		return u.sessionControls()
	case "screen":
		if u.hasFrame {
			add(func(g C) D {
				g.Constraints.Max.Y = min(g.Constraints.Max.Y, g.Dp(285))
				return (widget.Image{Src: u.frame, Fit: widget.Contain}).Layout(g)
			})
			open("attach", "将当前画面附到消息", func() { u.cmd("frame_attach", nil); u.open("") })
		}
		if truth(u.state["screen"]) {
			action("frame", "刷新当前画面", true, func() { u.cmd("snapshot", nil) })
			open("off", "停止共享", func() { u.cmd("screen_off", nil) })
		} else {
			action("select", "选择共享窗口", true, func() { u.cmd("screen", nil) })
			if truth(u.state["xwayland"]) {
				open("xwindows", "选择 XWayland 应用窗口", func() { u.cmd("screen_windows", nil) })
			}
		}
	case "windows":
		add(u.field("window-search", "查找窗口", false, false))
		for _, w := range u.windows {
			if !strings.Contains(strings.ToLower(str(w["title"])), strings.ToLower(u.edit("window-search").Text())) {
				continue
			}
			id := str(w["id"])
			if id == "" {
				id = str(w["ID"])
			}
			title := str(w["title"])
			if title == "" {
				title = str(w["Title"])
			}
			open(id, title, func() { u.cmd("screen", M{"window": id}); u.open("screen") })
		}
	case "speakers":
		for _, s := range rows(u.state["speakers"]) {
			add(u.text(str(s["speaker"])+"   "+clockText(number(s["seconds"])), 16, ink, false))
		}
		if len(ws) == 0 {
			add(u.text("暂无发言记录", 15, muted, false))
		}
	case "search":
		edit("query", "关键词", false, false)
		people := []choice{{"", "所有发言人"}}
		for _, s := range rows(u.state["speakers"]) {
			n := str(s["speaker"])
			people = append(people, choice{n, n})
		}
		add(u.pick("speaker", "发言人", people))
		add(u.pick("filter", "记录类型", []choice{{"", "全部"}, {"speech", "会议发言"}, {"assistant", "AI 回复"}, {"user", "我的消息"}}))
		submit("搜索")
		if u.results != nil {
			add(u.text(fmt.Sprintf("找到 %d 条", int(number(u.results["total"]))), 14, muted, false))
			items := rows(u.results["items"])
			for _, r := range items {
				at := number(r["at"])
				stamp := clockText(at)
				if !truth(r["timed"]) {
					stamp = "未标时"
				}
				open(fmt.Sprint(r["seq"]), stamp+"  "+str(r["text"]), func() { u.cmd("jump", M{"at": at}) })
			}
			offset := int(number(u.results["offset"]))
			if offset > 0 {
				open("prev", "上一页", func() { u.search(max(0, offset-60)) })
			}
			if offset+len(items) < int(number(u.results["total"])) {
				open("next", "下一页", func() { u.search(offset + len(items)) })
			}
		}
	case "path":
		edit("path", "文件路径", true, false)
		action("ok", "确定", true, u.acceptPath)
	}
	return ws
}
func (u *workbench) keyHint(p string) string {
	if truth(obj(obj(u.state["settings"])["hasKeys"])[p]) {
		return "API 密钥（已配置，留空保留）"
	}
	return "API 密钥"
}

func (u *workbench) acceptPath() {
	v := strings.TrimSpace(u.val("path"))
	if v == "" {
		return
	}
	var result any = manualSavePath(v)
	if str(u.dlg["kind"]) == "open-files" {
		var files []string
		for _, p := range strings.Split(v, "\n") {
			if p = strings.TrimSpace(p); p != "" {
				files = append(files, p)
			}
		}
		result = files
	}
	u.send(packet{Type: "dialog-result", ID: int(number(u.dlg["id"])), Value: result})
	u.open("")
}
