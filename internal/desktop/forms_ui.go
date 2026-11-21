package desktop

import (
	"fmt"
	"strconv"
	"strings"

	"gioui.org/layout"
	"gioui.org/widget"
)

var formTitles = map[string]string{"menu": "工作台菜单", "history": "会议与主题", "new": "新建会议", "source": "音频来源", "note": "编辑便签", "settings": "偏好设置", "agent": "AI 会话", "screen": "会议画面", "windows": "选择会议窗口", "topic": "加入主题", "rename": "重命名会议", "filters": "筛选会议记录", "board-filters": "筛选便签", "search": "查找本场会议记录", "confirm": "确认操作", "speakers": "发言人", "path": "选择文件"}

func (u *workbench) modalLayout(g C) D {
	title := formTitles[u.modal]
	if u.modal == "message" {
		title = str(u.dlg["title"])
	}
	if u.modal == "note" && u.note == nil {
		title = "新建便签"
	}
	if u.modal == "choice" {
		title = u.choiceTitle
	}
	return layout.Center.Layout(g, func(g C) D {
		w := min(g.Constraints.Max.X-g.Dp(24), g.Dp(710))
		h := g.Constraints.Max.Y - g.Dp(24)
		g.Constraints.Min.X = w
		g.Constraints.Max.X = w
		g.Constraints.Min.Y = h
		g.Constraints.Max.Y = h
		return box(g, paper, 14, inset(19, func(g C) D {
			items := u.formContent()
			return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Rigid(func(g C) D {
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text(title, 23, ink, true)), layout.Rigid(u.button("back", "返回", false, u.back)))
			}), layout.Rigid(layout.Spacer{Height: 14}.Layout), layout.Flexed(1, func(g C) D { return u.scroll(g, "form", items) }))
		}))
	})
}
func (u *workbench) pick(k, title string, opts []choice) W {
	s := labelOf(opts, u.fields[k])
	if s == "" {
		s = "选择"
	}
	return u.button("pick/"+k, title+"   ·   "+s, false, func() {
		u.beginChoice(k, title, opts)
	})
}
func (u *workbench) beginChoice(k, title string, opts []choice) {
	u.parentModal = u.modal
	u.choiceKey = k
	u.choiceTitle = title
	u.choices = opts
	u.open("choice")
	u.edit("choice-search").SetText("")
	u.nextFocus = u.edit("choice-search")
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
	for _, m := range rows(u.state["sources"]) {
		out = append(out, choice{str(m["id"]), str(m["label"])})
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
	case "menu":
		for _, item := range []struct {
			s string
			f func()
		}{
			{"新建会议", u.newMeeting}, {"会议与主题", func() { u.open("history") }}, {"导入逐字稿", u.importTranscript}, {"音频来源", u.sourceForm}, {"发言人", func() { u.open("speakers") }}, {"连接 AI", func() { u.open("agent") }}, {"会议画面", func() { u.open("screen") }}, {"导出 Markdown", func() { u.cmd("export", nil) }}, {"加入主题", func() { u.form("topic", map[string]string{"topic_id": "", "topic_name": ""}, "topic_name") }}, {"重命名会议", func() { u.form("rename", map[string]string{"title": str(u.meeting()["title"])}, "title") }}, {"重试待转写片段", func() { u.cmd("retry", nil); u.open("") }}, {"偏好设置", u.settingsForm},
		} {
			open(item.s, item.s, item.f)
		}
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
	case "source":
		add(u.pick("source", "选择麦克风或应用", u.sourceChoices()))
		open("refresh", "刷新来源", func() { u.cmd("sources", nil) })
		submit("使用此来源")
		action("start", "使用并开始转写", true, func() { u.cmd("set_source", M{"id": u.fields["source"], "start": true}); u.open("") })
	case "choice":
		add(u.field("choice-search", "筛选选项", false, false))
		if u.choiceKey == "source" {
			u.choices = u.sourceChoices()
		}
		for _, o := range u.choices {
			if !strings.Contains(strings.ToLower(o.label), strings.ToLower(u.edit("choice-search").Text())) {
				continue
			}
			action(o.id, o.label, u.fields[u.choiceKey] == o.id, func() { u.fields[u.choiceKey] = o.id; u.open(u.parentModal) })
		}
	case "board-filters":
		for _, o := range append([]choice{{"", "全部类型"}}, kinds...) {
			action("kind"+o.id, o.label, u.boardKind == o.id, func() { u.boardKind = o.id; u.open("") })
		}
		if u.topicBoard {
			for _, o := range append([]choice{{"pending", "未完成"}, {"", "全部状态"}}, statuses...) {
				action("status"+o.id, o.label, u.boardStatus == o.id, func() { u.boardStatus = o.id; u.open("") })
			}
		}
	case "note":
		edit("title", "标题", false, false)
		edit("body", "内容", true, false)
		add(u.pick("kind", "类型", kinds))
		if u.topicBoard {
			add(u.pick("status", "状态", statuses))
		}
		submit("保存便签")
		if u.note != nil {
			if !u.topicBoard && str(u.meeting()["topic_id"]) != "" {
				open("share", "加入主题看板", func() { u.noteCommand("share") })
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
	case "settings":
		add(u.pick("provider", "转写服务", []choice{{"siliconflow", "SiliconFlow"}, {"sophnet", "Sophnet"}}))
		p := u.fields["provider"]
		edit(p+"_model", "转写模型", false, false)
		edit(p+"_key", u.keyHint(p), false, true)
		edit("chunk", "分段秒数（4–45）", false, false)
		add(u.pick("agent", "默认 AI 服务", services))
		p = u.fields["agent"]
		if p == "openai" {
			edit("model", "OpenAI 模型", false, false)
			edit("openai_key", u.keyHint(p), false, true)
		} else {
			edit(p+"_path", "系统 CLI 路径（留空使用 PATH）", false, false)
			edit(p+".model", "Agent 模型", false, false)
			edit(p+".effort", "推理强度", false, false)
			edit(p+".speed", "速度", false, false)
			if p == "codex" {
				open("catalog", "读取本机 Codex 模型", func() { u.cmd("codex_models", M{"path": u.val("codex_path")}) })
				for _, item := range rows(obj(u.state["agentCatalogs"])[p]) {
					id := str(item["id"])
					open("model"+id, id, func() { u.edit("form/codex.model").SetText(id) })
				}
			}
		}
		for _, b := range []choice{{"auto", "AI 主动跟进"}, {"share_frames", "随 AI 消息共享画面"}} {
			checked := u.fields[b.id] == "true"
			action(b.id, b.label+"   "+map[bool]string{true: "开", false: "关"}[checked], checked, func() { u.fields[b.id] = strconv.FormatBool(!checked) })
		}
		submit("保存设置")
	case "agent":
		status := str(u.state["agent"])
		add(u.text(map[string]string{"offline": "选择服务，连接后可创建或继续会话", "ready": "已连接 · " + str(u.state["session"]), "connecting": "正在连接…", "choose": "选择会话", "opening": "正在打开会话…"}[status], 15, ink, false))
		if status == "ready" {
			if str(u.state["agentWorkspace"]) != "" {
				open("dir", "打开 Agent 工作目录", func() { u.cmd("open_agent_workspace", nil) })
			}
		} else if status == "choose" {
			saved := str(obj(u.state["meetingSessions"])[str(u.state["agentName"])])
			if saved != "" {
				action("resume", "继续上次会话", true, func() { u.cmd("session", M{"id": saved}) })
			}
			if str(u.meeting()["topic_id"]) != "" && saved != "" {
				open("replace", "重新建立主题会话", func() {
					u.choiceTitle = "为这个主题建立新会话？"
					u.confirm = func() { u.cmd("session", M{"replace": true}) }
					u.open("confirm")
				})
			} else {
				action("new", "创建新会话", true, func() { u.cmd("session", M{}) })
			}
			for _, s := range rows(u.state["sessions"]) {
				id := str(s["id"])
				name := str(s["title"])
				if name == "" {
					name = id
				}
				open(id, name, func() { u.cmd("session", M{"id": id}) })
			}
			if str(u.meeting()["topic_id"]) == "" {
				add(u.field("session-id", "已有会话 ID", false, false))
				open("use-id", "连接指定会话", func() { u.cmd("session", M{"id": u.edit("session-id").Text()}) })
			}
		} else if status == "offline" || status == "" {
			for _, p := range services {
				action(p.id, p.label, p.id == str(obj(u.state["settings"])["agent"]), func() { u.cmd("connect", M{"provider": p.id}) })
			}
			open("login", "登录系统 Codex", func() { u.cmd("login", nil) })
			if truth(u.state["loginLink"]) {
				open("browser", "打开登录链接", func() { u.cmd("login_browser", nil) })
			}
		}
		if status != "offline" {
			open("disconnect", "断开连接", func() { u.cmd("disconnect", nil) })
		}
		open("prefs", "AI 服务设置", u.settingsForm)
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
	case "filters":
		for _, k := range []choice{{"", "全部记录"}, {"speech", "会议发言"}, {"assistant", "AI 回复"}, {"user", "我的消息"}} {
			action(k.id, k.label, u.feedKind == k.id, func() { u.feedKind = k.id; u.open("") })
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
				open(fmt.Sprint(r["seq"]), clockText(at)+"  "+str(r["text"]), func() { u.cmd("jump", M{"at": at}) })
			}
			offset := int(number(u.results["offset"]))
			if offset > 0 {
				open("prev", "上一页", func() { u.search(max(0, offset-max(1, len(items)))) })
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
	var result any = v
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
