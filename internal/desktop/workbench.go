package desktop

import (
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	_ "image/jpeg"
	_ "image/png"
	"strconv"
	"strings"
	"sync"
	"time"

	"gioui.org/app"
	"gioui.org/font"
	"gioui.org/font/opentype"
	"gioui.org/gesture"
	"gioui.org/io/key"
	"gioui.org/io/system"
	"gioui.org/layout"
	"gioui.org/op"
	"gioui.org/op/clip"
	"gioui.org/op/paint"
	"gioui.org/text"
	"gioui.org/unit"
	"gioui.org/widget"
	"gioui.org/widget/material"
)

type C = layout.Context
type D = layout.Dimensions
type W = layout.Widget

var (
	ink    = rgb(0x172b2a)
	muted  = rgb(0x526b67)
	paper  = rgb(0xffffff)
	canvas = rgb(0xf4f7f5)
	line   = rgb(0xd6e2dc)
	teal   = rgb(0x126956)
	mint   = rgb(0xe0f0e8)
	dark   = rgb(0x153b34)
)

func rgb(n uint32) color.NRGBA {
	return color.NRGBA{R: uint8(n >> 16), G: uint8(n >> 8), B: uint8(n), A: 255}
}
func rows(v any) []M {
	items, _ := v.([]any)
	out := make([]M, 0, len(items))
	for _, item := range items {
		if m, ok := item.(M); ok {
			out = append(out, m)
		}
	}
	return out
}
func obj(v any) M { m, _ := v.(M); return m }
func clockText(t float64) string {
	i := max(0, int(t))
	if i >= 3600 {
		return fmt.Sprintf("%d:%02d:%02d", i/3600, i/60%60, i%60)
	}
	return fmt.Sprintf("%02d:%02d", i/60, i%60)
}

type workbench struct {
	timelineClick                       gesture.Click
	compact                             bool
	window                              *app.Window
	th                                  *material.Theme
	send                                func(packet)
	mu                                  sync.Mutex
	inbox                               []M
	wake                                chan struct{}
	quitting                            bool
	state                               M
	page, modal, focus                  string
	noticeText, noticeKind              string
	noticeUntil                         time.Time
	buttons                             map[string]*widget.Clickable
	editors                             map[string]*widget.Editor
	selectable                          map[string]*widget.Selectable
	lists                               map[string]*widget.List
	fields                              map[string]string
	drafts                              map[string]string
	note                                M
	topicBoard                          bool
	boardKind, boardStatus, feedKind    string
	stream, frameURI                    string
	frame                               paint.ImageOp
	hasFrame                            bool
	position                            float64
	dlg                                 M
	confirm                             func()
	windows                             []M
	results                             M
	querySerial                         int
	pendingSend                         string
	nextFocus                           *widget.Editor
	choices                             []choice
	choiceKey, choiceTitle, parentModal string
}

//go:embed fonts/TingsuSans.otf
var interfaceFont []byte

func newWorkbench(send func(packet)) *workbench {
	u := &workbench{window: new(app.Window), th: material.NewTheme(), send: send, wake: make(chan struct{}, 1),
		state: M{}, buttons: map[string]*widget.Clickable{}, editors: map[string]*widget.Editor{}, selectable: map[string]*widget.Selectable{}, lists: map[string]*widget.List{}, fields: map[string]string{}, drafts: map[string]string{}, page: "studio", boardStatus: "pending"}
	faces, err := opentype.ParseCollection(interfaceFont)
	if err == nil {
		u.th.Shaper = text.NewShaper(text.WithCollection(faces))
		u.th.Face = faces[0].Font.Typeface
	}
	u.th.Palette.ContrastBg = teal
	return u
}

// Only the window goroutine touches widget state. Workers enqueue immutable messages.
func (u *workbench) receive(m M) {
	u.mu.Lock()
	u.inbox = append(u.inbox, m)
	w := u.window
	u.mu.Unlock()
	select {
	case u.wake <- struct{}{}:
	default:
	}
	w.Invalidate()
}

func (u *workbench) drain() {
	u.mu.Lock()
	batch := u.inbox
	u.inbox = nil
	u.mu.Unlock()
	for _, m := range batch {
		switch str(m["type"]) {
		case "quit":
			u.quitting = true
		case "dialog":
			u.dlg = m
			switch str(m["kind"]) {
			case "open-files", "save-file":
				go func(m M) {
					value, err := chooseFile(str(m["kind"]) == "save-file", str(m["title"]), str(m["path"]), str(m["filter"]))
					u.receive(M{"type": "file-result", "request": m, "value": value, "error": err})
				}(m)
			default:
				u.open("message")
			}
		case "file-result":
			if _, ok := m["error"].(error); ok {
				u.dlg = obj(m["request"])
				u.toast("系统文件选择器不可用，可直接输入路径", "error")
				u.form("path", map[string]string{"path": str(u.dlg["path"])}, "path")
				continue
			}
			u.send(packet{Type: "dialog-result", ID: int(number(obj(m["request"])["id"])), Value: m["value"]})
		case "signal":
			args, _ := m["args"].([]any)
			if len(args) == 0 {
				continue
			}
			first := str(args[0])
			second := ""
			if len(args) > 1 {
				second = str(args[1])
			}
			switch str(m["name"]) {
			case "state":
				var next M
				if json.Unmarshal([]byte(first), &next) != nil {
					continue
				}
				old := str(u.meeting()["id"])
				u.state = next
				if id := str(u.meeting()["id"]); id != old {
					u.drafts[old] = u.edit("message").Text()
					u.edit("message").SetText(u.drafts[id])
					u.feedKind = ""
					u.boardKind = ""
					u.boardStatus = "pending"
					u.topicBoard = str(u.meeting()["topic_id"]) != ""
					u.position = 0
					u.stream = ""
					u.results = nil
					u.selectable = map[string]*widget.Selectable{}
					u.lists = map[string]*widget.List{}
					u.buttons = map[string]*widget.Clickable{}
				}
				if !truth(next["sending"]) {
					u.stream = ""
				}
				if uri := str(next["frame"]); uri != u.frameURI {
					u.frameURI = uri
					u.hasFrame = false
					if _, data, ok := strings.Cut(uri, ","); ok {
						if img, _, err := image.Decode(base64.NewDecoder(base64.StdEncoding, strings.NewReader(data))); err == nil {
							u.frame = paint.NewImageOp(img)
							u.hasFrame = true
						}
					}
				}
			case "notice":
				u.toast(first, second)
			case "event":
				switch first {
				case "workspace":
					u.page = "studio"
					u.open("")
				case "choose":
					u.open("agent")
				case "connected", "topic-saved", "settings-saved":
					u.open("")
				case "note-saved":
					if second == "native-note" {
						u.open("")
					}
				case "delta":
					u.stream = second
				case "sent":
					if u.edit("message").Text() == u.pendingSend {
						u.edit("message").SetText("")
					}
				case "position":
					u.position, _ = strconv.ParseFloat(second, 64)
				case "screen-windows":
					_ = json.Unmarshal([]byte(second), &u.windows)
					u.open("windows")
					u.edit("window-search").SetText("")
					u.nextFocus = u.edit("window-search")
				case "search-results":
					var result M
					if json.Unmarshal([]byte(second), &result) == nil && int(number(result["id"])) == u.querySerial {
						u.results = result
					}
				case "jump":
					at, _ := strconv.ParseFloat(second, 64)
					u.jump(at)
				}
			}
		}
	}
}
func (u *workbench) configuredWindow(w *app.Window) {
	w.Option(app.Title("听序 · 会议工作台"), app.Size(1240, 790), app.MinSize(680, 480))
}
func (u *workbench) run() error {
	u.configuredWindow(u.window)
	u.send(packet{Type: "ready"})
	var ops op.Ops
	for {
		w := u.window
		switch e := w.Event().(type) {
		case app.DestroyEvent:
			if e.Err != nil {
				return e.Err
			}
			if u.quitting {
				return nil
			}
			u.send(packet{Type: "close-request"})
			for {
				<-u.wake
				u.drain()
				if u.quitting {
					return nil
				}
				if u.modal == "message" {
					break
				}
			}
			n := new(app.Window)
			u.configuredWindow(n)
			u.mu.Lock()
			u.window = n
			u.mu.Unlock()
		case app.FrameEvent:
			gtx := app.NewContext(&ops, e)
			u.drain()
			if !u.quitting {
				u.shortcuts(gtx)
				u.layout(gtx)
			}
			e.Frame(gtx.Ops)
			if u.quitting {
				w.Perform(system.ActionClose)
			}
		}
	}
}
func (u *workbench) meeting() M           { return obj(u.state["meeting"]) }
func (u *workbench) cmd(name string, a M) { u.send(packet{Type: "command", Name: name, Args: a}) }
func (u *workbench) toast(s, kind string) {
	u.noticeText = s
	u.noticeKind = kind
	u.noticeUntil = time.Now().Add(7 * time.Second)
}
func (u *workbench) edit(id string) *widget.Editor {
	if u.editors[id] == nil {
		u.editors[id] = &widget.Editor{SingleLine: true}
	}
	return u.editors[id]
}
func (u *workbench) list(id string) *widget.List {
	if u.lists[id] == nil {
		l := new(widget.List)
		l.Axis = layout.Vertical
		u.lists[id] = l
	}
	return u.lists[id]
}
func (u *workbench) open(id string) {
	u.modal = id
	u.window.Invalidate()
	delete(u.lists, "form")
	u.nextFocus = nil
}
func (u *workbench) form(id string, values map[string]string, focus string) {
	u.open(id)
	u.fields = values
	for key, v := range values {
		u.edit("form/" + key).SetText(v)
	}
	if focus != "" {
		u.nextFocus = u.edit("form/" + focus)
	}
}
func (u *workbench) val(k string) string { return u.edit("form/" + k).Text() }
func (u *workbench) click(id string) *widget.Clickable {
	if u.buttons[id] == nil {
		u.buttons[id] = new(widget.Clickable)
	}
	return u.buttons[id]
}
func (u *workbench) text(s string, size unit.Sp, c color.NRGBA, bold bool) W {
	return func(g C) D {
		l := material.Label(u.th, size, s)
		l.Color = c
		if bold {
			l.Font.Weight = font.SemiBold
		}
		return l.Layout(g)
	}
}
func (u *workbench) button(id, s string, primary bool, fn func()) W {
	return func(g C) D {
		btn := u.click(id)
		for btn.Clicked(g) {
			fn()
		}
		style := material.Button(u.th, btn, s)
		style.TextSize = 13
		style.CornerRadius = 7
		style.Inset = layout.Inset{Top: 7, Bottom: 7, Left: 11, Right: 11}
		if !primary {
			style.Background = mint
			style.Color = teal
		}
		return style.Layout(g)
	}
}
func inset(n unit.Dp, w W) W { return func(g C) D { return layout.UniformInset(n).Layout(g, w) } }
func row(sp unit.Dp, ws ...W) W {
	return func(g C) D {
		cs := make([]layout.FlexChild, 0, 2*len(ws))
		for i, w := range ws {
			if i > 0 {
				cs = append(cs, layout.Rigid(layout.Spacer{Width: sp}.Layout))
			}
			cs = append(cs, layout.Rigid(w))
		}
		return (layout.Flex{Alignment: layout.Middle}).Layout(g, cs...)
	}
}
func col(sp unit.Dp, ws ...W) W {
	return func(g C) D {
		cs := make([]layout.FlexChild, 0, 2*len(ws))
		for i, w := range ws {
			if i > 0 {
				cs = append(cs, layout.Rigid(layout.Spacer{Height: sp}.Layout))
			}
			cs = append(cs, layout.Rigid(w))
		}
		return (layout.Flex{Axis: layout.Vertical}).Layout(g, cs...)
	}
}
func box(g C, c color.NRGBA, r unit.Dp, child W) D {
	rec := op.Record(g.Ops)
	d := child(g)
	call := rec.Stop()
	paint.FillShape(g.Ops, c, clip.UniformRRect(image.Rectangle{Max: d.Size}, g.Dp(r)).Op(g.Ops))
	call.Add(g.Ops)
	return d
}
func full(g C) C { g.Constraints.Min.X = g.Constraints.Max.X; return g }
func (u *workbench) field(id, hint string, multi, secret bool) W {
	return func(g C) D {
		ed := u.edit(id)
		ed.SingleLine = !multi
		if secret {
			ed.Mask = '•'
		}
		return box(full(g), canvas, 6, inset(9, func(g C) D {
			if multi {
				g.Constraints.Min.Y = g.Dp(46)
				g.Constraints.Max.Y = min(g.Constraints.Max.Y, g.Dp(105))
			}
			style := material.Editor(u.th, ed, hint)
			style.TextSize = 15
			style.Color = ink
			style.HintColor = muted
			return style.Layout(g)
		}))
	}
}
func (u *workbench) scroll(g C, id string, ws []W) D {
	return material.List(u.th, u.list(id)).Layout(g, len(ws), func(g C, i int) D { return layout.Inset{Bottom: 7, Right: 6}.Layout(g, ws[i]) })
}
func (u *workbench) layout(g C) D {
	paint.Fill(g.Ops, canvas)
	if u.nextFocus != nil {
		g.Execute(key.FocusCmd{Tag: u.nextFocus})
		u.nextFocus = nil
	}
	return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Flexed(1, func(g C) D {
		if u.modal != "" {
			return u.modalLayout(g)
		}
		return u.workspace(g)
	}), layout.Rigid(func(g C) D {
		if u.noticeText == "" || g.Now.After(u.noticeUntil) {
			return D{}
		}
		g.Execute(op.InvalidateCmd{At: u.noticeUntil})
		c := teal
		if u.noticeKind == "error" {
			c = rgb(0x983c33)
		}
		return box(full(g), c, 0, inset(9, u.text(u.noticeText, 14, paper, false)))
	}))
}
