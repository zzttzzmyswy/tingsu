package desktop

import (
	"fmt"
	"strconv"
	"strings"

	"gioui.org/layout"
	"gioui.org/widget/material"
)

var prefPages = []choice{{"asr", "语音转写（ASR）"}, {"local", "本地 Agent"}, {"meeting", "会话行为"}}
var asrVendors = []choice{{"siliconflow", "SiliconFlow"}, {"sophnet", "Sophnet"}}

const inheritModel = "跟随本机 CLI"

func values(ids ...string) []choice {
	var o []choice
	for _, id := range ids {
		o = append(o, choice{id, id})
	}
	return o
}
func (u *workbench) prefPage(id string) {
	u.prefsTab = id
	u.closePopup()
	delete(u.lists, "preferences")
	if id == "local" && u.localCLI == "codex" {
		u.fetchCatalog()
	}
}
func (u *workbench) fetchCatalog() {
	u.catalogBusy = true
	u.catalogError = ""
	u.cmd("codex_models", M{"path": u.val("codex_path")})
}
func (u *workbench) prefsLayout(g C) D {
	return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Flexed(1, func(g C) D {
		return (layout.Flex{}).Layout(g, layout.Rigid(fixedWidth(153, func(g C) D {
			var ws []W
			for _, s := range prefPages {
				ws = append(ws, u.button("pref-nav/"+s.id, s.label, u.prefsTab == s.id, func() { u.prefPage(s.id) }))
			}
			g.Constraints.Min.Y = g.Constraints.Max.Y
			return box(g, canvas, 0, padding{Top: 14, Bottom: 14, Left: 8, Right: 8}.Wrap(func(g C) D { return layout.N.Layout(g, col(6, ws...)) }))
		})), layout.Flexed(1, inset(16, func(g C) D { return u.scroll(g, "preferences", u.prefControls()) })))
	}), layout.Rigid(rule), layout.Rigid(padding{Top: 9, Right: 15, Bottom: 9}.Wrap(func(g C) D {
		return layout.E.Layout(g, row(8, u.button("prefs/cancel", "取消", false, u.back), u.button("prefs/save", "保存设置", true, u.savePrefs)))
	})))
}
func (u *workbench) prefControls() []W {
	ws := []W{u.text(labelOf(prefPages, u.prefsTab), 19, ink, true)}
	field := func(label string, control W) {
		ws = append(ws, func(g C) D {
			return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Rigid(fixedWidth(90, u.oneLine(label, 13, muted))), layout.Flexed(1, control))
		})
	}
	option := func(k string, opts []choice, fn func(string)) W {
		return u.drop("pref/"+k, labelOf(opts, u.fields[k]), opts, u.fields[k], false, func(v string) {
			u.fields[k] = v
			if fn != nil {
				fn(v)
			}
		})
	}
	model := func(k, title, def string, opts []choice) {
		opts = append([]choice{{"", def}}, opts...)
		current := u.val(k)
		if current != "" {
			found := false
			for _, o := range opts {
				if o.id == current {
					found = true
					break
				}
			}
			if !found {
				opts = append(opts, choice{current, current})
			}
		}
		opts = append(opts, choice{"@custom", "自定义…"})
		label := labelOf(opts, current)
		if u.custom[k] {
			label = "自定义…"
		}
		field(title, u.drop("pref/"+k, label, opts, current, k == "codex.model" || len(opts) > 8, func(v string) {
			u.custom[k] = v == "@custom"
			if u.custom[k] {
				u.nextFocus = u.edit("form/" + k)
				return
			}
			u.edit("form/" + k).SetText(v)
		}))
		if u.custom[k] {
			field("自定义值", u.field("form/"+k, "输入服务支持的值", false, false))
		}
	}
	keyField := func(p string) { field("API 密钥", u.field("form/"+p+"_key", u.keyHint(p), false, true)) }
	switch u.prefsTab {
	case "asr":
		field("转写供应商", option("provider", asrVendors, nil))
		p := u.fields["provider"]
		opts := values("Fun-ASR")
		if p == "siliconflow" {
			opts = []choice{{"XingChenAGI/XingChenASR-Diarize-V3.0", "XingChenASR-Diarize V3.0 · 说话人区分"}, {"XingChenAGI/XingChenASR-V3.2-Ultra", "XingChenASR V3.2 Ultra"}, {"XingChenAGI/XingChenGSR-V1.0", "XingChenGSR V1.0"}}
		}
		model(p+"_model", "转写模型", "服务默认", opts)
		keyField(p)
		var chunks []choice
		for n := 4; n <= 45; n++ {
			chunks = append(chunks, choice{strconv.Itoa(n), fmt.Sprintf("%d 秒", n)})
		}
		field("分段长度", option("chunk", chunks, nil))
	case "local":
		p := u.localCLI
		field("本机程序", u.drop("pref/local-cli", labelOf(services, p), services, p, false, func(id string) {
			u.localCLI = id
			delete(u.lists, "preferences")
			if id == "codex" {
				u.fetchCatalog()
			}
		}))
		path := u.val(p + "_path")
		hint := "自动查找 " + str(obj(providers[p])["cli"])
		if path != "" {
			hint = path
		}
		field("程序路径", func(g C) D {
			return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.oneLine(hint, 13, ink)), layout.Rigid(u.button("path/pick", "浏览…", false, func() {
				go func() {
					files, err := chooseFile(false, "选择本机 Agent 程序", "", "")
					u.receive(M{"type": "cli-path", "provider": p, "value": files, "error": err})
				}()
			})), layout.Rigid(u.button("path/more", "更改", false, func() { u.custom[p+"_path"] = !u.custom[p+"_path"] })))
		})
		if u.custom[p+"_path"] {
			field("指定路径", u.field("form/"+p+"_path", "留空自动查找", false, false))
		}
		catalog := obj(u.state["agentCatalogs"])
		var models, efforts, speeds []choice
		switch p {
		case "codex":
			var current M
			for _, m := range rows(catalog[p]) {
				id := str(m["id"])
				models = append(models, choice{id, id})
				if id == u.val("codex.model") || u.val("codex.model") == "" && truth(m["default"]) {
					current = m
				}
			}
			efforts = values(stringsOf(current["effort"])...)
			speeds = values(stringsOf(current["speed"])...)
			status := fmt.Sprintf("已读取 %d 个模型", len(models))
			if u.catalogBusy {
				status = "正在读取本机目录…"
			} else if u.catalogError != "" {
				status = u.catalogError
			}
			field("模型目录", func(g C) D {
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.text(status, 13, muted, false)), layout.Rigid(u.button("catalog/refresh", "刷新", false, u.fetchCatalog)))
			})
		case "claude":
			models = values("sonnet", "opus", "haiku")
			efforts = values("low", "medium", "high", "xhigh", "max")
			speeds = values("standard", "fast")
		case "deepseek":
			for i, k := range []string{"model", "effort", "speed"} {
				var opts []choice
				for _, m := range rows(obj(catalog[p])[k]) {
					opts = append(opts, choice{str(m["id"]), str(m["label"])})
				}
				switch i {
				case 0:
					models = opts
				case 1:
					efforts = opts
				case 2:
					speeds = opts
				}
			}
		}
		model(p+".model", "模型", inheritModel, models)
		model(p+".effort", "推理强度", inheritModel, efforts)
		model(p+".speed", "响应速率", inheritModel, speeds)
		if p == "codex" {
			if truth(u.state["loginLink"]) {
				field("账号", u.button("auth/open", "打开登录链接", false, func() { u.cmd("login_browser", nil) }))
			} else {
				field("账号", u.button("auth/login", "登录本机 Codex", false, func() { u.cmd("login", nil) }))
			}
		}
	case "meeting":
		field("默认助手", option("agent", services, nil))
		for _, o := range []choice{{"auto", "有新发言时主动分析并跟进待办"}, {"share_frames", "发送消息时附上当前共享画面"}} {
			ws = append(ws, func(g C) D {
				b := u.check(o.id)
				b.Value = u.fields[o.id] == "true"
				if b.Update(g) {
					u.fields[o.id] = strconv.FormatBool(b.Value)
				}
				return material.CheckBox(u.th, b, o.label).Layout(g)
			})
		}
	}
	return ws
}

func (u *workbench) openAgent() {
	p := str(u.meeting()["topic_provider"])
	if providers[p] == nil {
		p = str(obj(u.state["settings"])["agent"])
	}
	if p == "" {
		p = "codex"
	}
	u.form("agent", map[string]string{"session-provider": p, "session-choice": ""}, "")
}
func (u *workbench) sessionControls() []W {
	status := str(u.state["agent"])
	p := u.fields["session-provider"]
	if p == "" {
		p = str(u.state["agentName"])
	}
	source := func() W {
		return u.drop("session-provider", labelOf(services, p), services, p, false, func(id string) { u.fields["session-provider"] = id })
	}
	switch status {
	case "offline", "":
		return []W{u.text("选择本次会议使用的助手", 14, muted, false), source(), u.button("connect", "连接", true, func() { u.cmd("connect", M{"provider": p}) })}
	case "choose":
		opts := []choice{{"", "创建新会话"}}
		saved := str(obj(u.state["meetingSessions"])[str(u.state["agentName"])])
		if saved != "" {
			opts = append(opts, choice{saved, "继续上次会话"})
		}
		if str(u.meeting()["topic_id"]) == "" {
			for _, s := range rows(u.state["sessions"]) {
				id := str(s["id"])
				if id != saved {
					name := str(s["title"])
					if name == "" {
						name = id
					}
					opts = append(opts, choice{id, name})
				}
			}
			opts = append(opts, choice{"@id", "输入已有会话 ID…"})
		}
		selected := u.fields["session-choice"]
		ws := []W{u.text(labelOf(services, str(u.state["agentName"])), 16, ink, true), u.drop("session-choice", labelOf(opts, selected), opts, selected, len(opts) > 8, func(id string) { u.fields["session-choice"] = id })}
		if selected == "@id" {
			ws = append(ws, u.field("session-id", "会话 ID", false, false))
		}
		return append(ws, u.button("session-go", "进入会话", true, func() {
			if selected == "@id" {
				selected = strings.TrimSpace(u.edit("session-id").Text())
				if selected == "" {
					u.toast("请输入会话 ID", "error")
					return
				}
			}
			if selected == "" && saved != "" && str(u.meeting()["topic_id"]) != "" {
				u.confirm = func() { u.cmd("session", M{"replace": true}) }
				u.choiceTitle = "为这个主题建立新会话？"
				u.open("confirm")
				return
			}
			u.cmd("session", M{"id": selected})
		}), u.button("disconnect", "断开", false, func() { u.cmd("disconnect", nil); u.openAgent() }))
	case "ready":
		ws := []W{u.text(labelOf(services, str(u.state["agentName"]))+" 已连接", 16, selection, true), u.text(str(u.state["session"]), 13, muted, false)}
		if str(u.state["agentWorkspace"]) != "" {
			ws = append(ws, u.button("session-dir", "打开工作目录", false, func() { u.cmd("open_agent_workspace", nil) }))
		}
		return append(ws, u.button("disconnect", "断开连接", false, func() { u.cmd("disconnect", nil); u.openAgent() }))
	default:
		return []W{u.text("正在连接…", 15, muted, false), u.button("disconnect", "取消连接", false, func() { u.cmd("disconnect", nil); u.openAgent() })}
	}
}
