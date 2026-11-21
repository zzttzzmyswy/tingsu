package desktop

import (
	"fmt"
	"image"
	"strings"

	"gioui.org/gesture"
	"gioui.org/io/pointer"
	"gioui.org/layout"
	"gioui.org/op"
	"gioui.org/op/clip"
	"gioui.org/op/paint"
	"gioui.org/unit"
)

type padding layout.Inset

func (p padding) Wrap(child W) W { return func(g C) D { return layout.Inset(p).Layout(g, child) } }

func rule(g C) D {
	w := g.Constraints.Max.X
	h := max(1, g.Dp(1))
	paint.FillShape(g.Ops, line, clip.Rect(image.Rect(0, 0, w, h)).Op())
	return D{Size: image.Pt(w, h)}
}
func (u *workbench) menuBar(g C) D {
	file := []menuItem{
		{"new", "新建会议", "Ctrl+N", u.newMeeting},
		{"import", "导入逐字稿…", "Ctrl+I", u.importTranscript},
		{"export", "导出会议记录…", "", func() { u.cmd("export", nil) }},
	}
	meeting := []menuItem{
		{"history", "打开已有会议…", "", func() { u.open("history") }},
		{"rename", "重命名会议…", "", func() { u.form("rename", map[string]string{"title": str(u.meeting()["title"])}, "title") }},
		{"topic", "加入主题…", "", func() { u.form("topic", map[string]string{"topic_id": "", "topic_name": ""}, "topic_name") }},
		{"note", "新建便签…", "Ctrl+K", func() { u.editNote(nil) }},
		{"retry", "重试待转写片段", "", func() { u.cmd("retry", nil) }},
	}
	view := []menuItem{
		{"find", "查找会议记录…", "Ctrl+F", u.openSearch},
		{"speakers", "发言人统计…", "", func() { u.open("speakers") }},
		{"history", "显示 / 隐藏会议列表", "", func() { u.hideRail = !u.hideRail }},
		{"board", "显示 / 隐藏便签看板", "", func() { u.hideBoard = !u.hideBoard }},
	}
	settings := []menuItem{{"prefs", "首选项…", "Ctrl+,", u.settingsForm}}
	var ws []W
	ws = append(ws, func(g C) D {
		return padding{Left: 8, Right: 12}.Wrap(row(7, func(g C) D { return glyph(g, waveIcon, 20, teal) }, u.text("听序", 16, ink, true)))(g)
	})
	for _, m := range []struct {
		id, title string
		items     []menuItem
	}{{"file", "文件", file}, {"meeting", "会议", meeting}, {"view", "视图", view}, {"settings", "设置", settings}} {
		ws = append(ws, func(g C) D { return u.anchor(g, "menu/"+m.id, m.title, m.items, "\x00", false, true) })
	}
	return box(full(g), paper, 0, padding{Top: 3, Bottom: 3, Left: 6, Right: 8}.Wrap(row(3, ws...)))
}
func (u *workbench) toggleRecording() {
	switch str(u.state["capture"]) {
	case "recording":
		u.cmd("stop", nil)
	case "stopping":
		return
	default:
		if truth(u.meeting()["demo"]) {
			u.newMeeting()
			return
		}
		if str(u.meeting()["source"]) == "" {
			u.openAudio()
			return
		}
		u.cmd("start", nil)
	}
}
func (u *workbench) openAudio() {
	u.cmd("sources", nil)
	u.showPopup("capture/audio", true)
}
func (u *workbench) captureAudio(g C) D {
	sel := str(u.meeting()["source"])
	name := "选择麦克风或应用"
	if sel != "" {
		name = str(u.meeting()["source_label"])
	}
	opts := u.sourceChoices()[1:]
	opts = append(opts, choice{"@refresh", "刷新可用音频设备"})
	rec := "开始转写"
	capture := str(u.state["capture"])
	if capture == "recording" {
		rec = "停止转写"
	} else if capture == "stopping" {
		rec = "正在停止"
	}
	return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Rigid(row(5, func(g C) D { return glyph(g, micIcon, 18, teal) }, u.text("音频", 14, ink, true))), layout.Rigid(layout.Spacer{Width: 8}.Layout),
		layout.Flexed(1, u.drop("capture/audio", name, opts, sel, true, func(id string) {
			if id == "@refresh" {
				u.openAudio()
				return
			}
			if truth(u.meeting()["demo"]) {
				u.newMeeting()
				u.fields["source"] = id
				return
			}
			u.cmd("set_source", M{"id": id})
		})), layout.Rigid(layout.Spacer{Width: 6}.Layout), layout.Rigid(u.button("record-main", rec, true, u.toggleRecording)))
}
func (u *workbench) shareWindow() { u.open("screen"); u.cmd("screen", nil) }
func (u *workbench) captureVideo(g C) D {
	on := truth(u.state["screen"])
	name := "选择窗口…"
	if on {
		name = str(u.state["screenLabel"])
		if name == "" {
			name = "正在共享"
		}
	}
	opts := []choice{{"pick", "选择共享窗口…"}}
	if truth(u.state["xwayland"]) {
		opts = append(opts, choice{"xwin", "选择 XWayland 窗口…"})
	}
	if on {
		opts = []choice{{"preview", "预览当前画面"}, {"snap", "重新截图"}, {"replace", "更换共享窗口…"}, {"off", "停止共享"}}
	}
	var selectWindow W
	if !on && len(opts) == 1 {
		selectWindow = u.button("capture/window", name, false, u.shareWindow)
	} else {
		selectWindow = u.drop("capture/window", name, opts, "", false, func(id string) {
			switch id {
			case "pick":
				u.shareWindow()
			case "xwin":
				u.cmd("screen_windows", nil)
			case "preview":
				u.open("screen")
			case "snap":
				u.open("screen")
				u.cmd("snapshot", nil)
			case "replace":
				u.cmd("screen_off", nil)
				u.shareWindow()
			case "off":
				u.cmd("screen_off", nil)
			}
		})
	}
	return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Rigid(row(5, func(g C) D { return glyph(g, displayIcon, 18, teal) }, u.text("窗口画面", 14, ink, true))), layout.Rigid(layout.Spacer{Width: 8}.Layout), layout.Flexed(1, selectWindow))
}
func (u *workbench) toolbar(g C) D {
	return box(full(g), rgb(0xf6f8f8), 0, padding{Top: 8, Bottom: 8, Left: 15, Right: 15}.Wrap(func(g C) D {
		agent := "连接助手"
		if n := str(u.state["agentName"]); n != "" {
			agent = labelOf(services, n) + " 会话"
		}
		return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1.4, u.captureAudio), layout.Rigid(layout.Spacer{Width: 20}.Layout), layout.Flexed(1, u.captureVideo), layout.Rigid(layout.Spacer{Width: 15}.Layout), layout.Rigid(u.button("session", agent, false, u.openAgent)))
	}))
}

func (u *workbench) workspace(g C) D {
	return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Rigid(u.menuBar), layout.Rigid(rule), layout.Rigid(u.toolbar), layout.Rigid(rule), layout.Flexed(1, u.panes))
}
func (u *workbench) panes(g C) D {
	total := g.Constraints.Max.X
	showRail := !u.hideRail && total >= g.Dp(910)
	parts := []layout.FlexChild{}
	if showRail {
		w := min(max(u.railWidth, 155), min(320, float32(total)/g.Metric.PxPerDp*.24))
		parts = append(parts, layout.Rigid(fixedWidth(unit.Dp(w), u.rail)), layout.Rigid(u.divider(&u.railDrag, &u.railWidth, 1, 155, 320)))
	}
	parts = append(parts, layout.Flexed(1, u.feed))
	if !u.hideBoard {
		w := min(max(u.boardWidth, 210), float32(total)/g.Metric.PxPerDp*.36)
		parts = append(parts, layout.Rigid(u.divider(&u.boardDrag, &u.boardWidth, -1, 210, 480)), layout.Rigid(fixedWidth(unit.Dp(w), u.board)))
	}
	return (layout.Flex{}).Layout(g, parts...)
}

type panelDrag struct {
	gesture.Drag
	at float32
}

func (u *workbench) divider(d *panelDrag, value *float32, sign, low, high float32) W {
	return func(g C) D {
		for {
			e, ok := d.Update(g.Metric, g.Source, gesture.Horizontal)
			if !ok {
				break
			}
			// The handle moves with the divider. Track the initial grab offset in handle coordinates.
			switch e.Kind {
			case pointer.Press:
				d.at = e.Position.X
			case pointer.Drag:
				*value = min(high, max(low, *value+sign*(e.Position.X-d.at)/g.Metric.PxPerDp))
			}
		}
		w, h := g.Dp(5), g.Constraints.Max.Y
		area := clip.Rect(image.Rect(0, 0, w, h)).Push(g.Ops)
		d.Add(g.Ops)
		pointer.CursorColResize.Add(g.Ops)
		paint.FillShape(g.Ops, line, clip.Rect(image.Rect(w/2, 0, w/2+1, h)).Op())
		area.Pop()
		return D{Size: image.Pt(w, h)}
	}
}
func (u *workbench) rail(g C) D {
	g.Constraints.Min.Y = g.Constraints.Max.Y
	return box(g, rgb(0xf4f7f7), 0, padding{Top: 16, Left: 10, Right: 8, Bottom: 6}.Wrap(func(g C) D {
		return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Rigid(func(g C) D {
			return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, row(6, func(g C) D { return glyph(g, folderIcon, 17, teal) }, u.text("会议列表", 15, ink, true))), layout.Rigid(u.button("nav-new", "＋", false, u.newMeeting)))
		}),
			layout.Rigid(padding{Top: 9, Bottom: 10}.Wrap(u.field("rail-find", "查找会议或主题", false, false))),
			layout.Flexed(1, func(g C) D {
				q := strings.ToLower(strings.TrimSpace(u.edit("rail-find").Text()))
				var ws []W
				for _, m := range rows(u.state["meetings"]) {
					title, topic, id := str(m["title"]), str(m["topic_name"]), str(m["id"])
					if !strings.Contains(strings.ToLower(title+" "+topic), q) {
						continue
					}
					label := topic
					if truth(m["demo"]) {
						label = "示例会议"
					} else if label == "" {
						label = "独立会议"
					}
					ws = append(ws, func(g C) D {
						b := u.click("recent/" + id)
						for b.Clicked(g) {
							u.cmd("select", M{"id": id})
						}
						bg := rgb(0xf4f7f7)
						if id == str(u.meeting()["id"]) {
							bg = mint
						} else if b.Hovered() {
							bg = paper
						}
						return b.Layout(g, func(g C) D {
							return box(full(g), bg, 4, inset(8, col(3, u.oneLine(title, 14, ink), u.oneLine(label, 12, muted))))
						})
					})
				}
				if len(ws) == 0 {
					ws = append(ws, u.text("没有匹配的会议", 13, muted, false))
				}
				return u.scroll(g, "rail", ws)
			}))
	}))
}
func (u *workbench) statusBar(g C) D {
	status := "就绪"
	c := muted
	if g.Now.Before(u.noticeUntil) {
		status = u.noticeText
		if u.noticeKind == "error" {
			c = rgb(0x983c33)
		}
		g.Execute(op.InvalidateCmd{At: u.noticeUntil})
	} else if str(u.state["capture"]) == "recording" {
		status = "正在转写"
		c = teal
	}
	asr := map[string]string{"sophnet": "Sophnet", "siliconflow": "SiliconFlow"}[str(obj(u.state["settings"])["provider"])]
	duration := clockText(max(number(u.meeting()["duration"]), u.position))
	return box(full(g), rgb(0xedf2f0), 0, padding{Top: 5, Bottom: 5, Left: 13, Right: 13}.Wrap(func(g C) D {
		return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.oneLine(status, 12, c)), layout.Rigid(u.oneLine(fmt.Sprintf("%s   ·   %s", asr, duration), 12, muted)))
	}))
}
