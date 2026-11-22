package desktop

import (
	"image"
	"image/color"
	"strings"

	"gioui.org/io/event"
	"gioui.org/io/key"
	"gioui.org/io/pointer"
	"gioui.org/layout"
	"gioui.org/op"
	"gioui.org/op/clip"

	"gioui.org/unit"
	"gioui.org/widget"
	"gioui.org/widget/material"
	"golang.org/x/exp/shiny/materialdesign/icons"
)

type popupState struct {
	id, query string
	index     int
	origin    image.Point
	anchored  bool
}

type menuItem struct {
	id, label, hint string
	act             func()
}

var (
	chevron     = makeIcon(icons.HardwareKeyboardArrowDown)
	micIcon     = makeIcon(icons.AVMicNone)
	displayIcon = makeIcon(icons.HardwareDesktopWindows)
	waveIcon    = makeIcon(icons.AVEqualizer)
	folderIcon  = makeIcon(icons.FileFolderOpen)
)

func makeIcon(data []byte) *widget.Icon {
	i, err := widget.NewIcon(data)
	if err != nil {
		panic(err)
	}
	return i
}
func glyph(g C, icon *widget.Icon, size unit.Dp, c color.NRGBA) D {
	g.Constraints = layout.Exact(image.Pt(g.Dp(size), g.Dp(size)))
	return icon.Layout(g, c)
}
func (u *workbench) oneLine(s string, size unit.Sp, c color.NRGBA) W {
	return func(g C) D {
		l := material.Label(u.th, u.fontSize(size), s)
		l.MaxLines = 1
		l.Color = c
		return l.Layout(g)
	}
}
func (u *workbench) showPopup(id string, search bool) {
	u.popup = popupState{id: id}
	delete(u.lists, "popup")
	u.edit("popup-search").SetText("")
	if search {
		u.nextFocus = u.edit("popup-search")
	}
	u.window.Invalidate()
}
func (u *workbench) closePopup() {
	u.popup = popupState{}
	u.window.Invalidate()
}

// The trigger records the popup in its own coordinate system. Defer restores
// this transform after layout, outside any clipping applied by a list or pane.
func (u *workbench) drop(id, caption string, opts []choice, selected string, search bool, changed func(string)) W {
	return func(g C) D {
		items := make([]menuItem, 0, len(opts))
		for _, o := range opts {
			items = append(items, menuItem{id: o.id, label: o.label, act: func() { changed(o.id) }})
		}
		return u.anchor(g, id, caption, items, selected, search, false)
	}
}
func (u *workbench) anchor(g C, id, caption string, items []menuItem, selected string, search, menubar bool) D {
	btn := u.click("opener/" + id)
	for btn.Clicked(g) {
		if u.popup.id == id {
			u.closePopup()
		} else {
			if id == "capture/audio" {
				u.cmd("sources", nil)
			}
			u.showPopup(id, search)
		}
	}
	bg := paper
	if btn.Hovered() || g.Focused(btn) || u.popup.id == id {
		bg = hover
	}
	d := btn.Layout(g, func(g C) D {
		if menubar {
			return box(g, bg, 3, padding{Top: 5, Bottom: 5, Left: 9, Right: 9}.Wrap(u.oneLine(caption, 14, ink)))
		}
		return control(full(g), bg, func(g C) D {
			return inset(1, padding{Top: 6, Bottom: 6, Left: 9, Right: 7}.Wrap(func(g C) D {
				return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Flexed(1, u.oneLine(caption, 14, ink)), layout.Rigid(func(g C) D { return glyph(g, chevron, 16, muted) }))
			}))(g)
		})
	})
	if u.popup.id != id {
		return d
	}
	fullSize := u.surface
	width := max(d.Size.X, g.Dp(215))
	if search {
		width = max(width, g.Dp(300))
	}
	width = min(width, fullSize.X-g.Dp(8))
	if !u.popup.anchored {
		if hist := btn.History(); len(hist) > 0 {
			u.popup.origin = u.pointerAt.Sub(hist[len(hist)-1].Position)
		}
		u.popup.anchored = true
	}
	origin := u.popup.origin
	dx := min(0, fullSize.X-g.Dp(5)-origin.X-width)
	maxH := min(g.Dp(294), fullSize.Y/2)
	room := fullSize.Y - origin.Y - d.Size.Y - g.Dp(8)
	up := !menubar && room < maxH && origin.Y > room
	m := op.Record(g.Ops)
	child := g
	child.Constraints = layout.Constraints{Min: image.Pt(width, 0), Max: image.Pt(width, maxH)}
	body := op.Record(g.Ops)
	dims := u.popupBody(child, items, selected, search)
	call := body.Stop()
	dy := d.Size.Y + g.Dp(2)
	if up {
		dy = -dims.Size.Y - g.Dp(2)
	}
	t := op.Offset(image.Pt(dx, dy)).Push(g.Ops)
	call.Add(g.Ops)
	t.Pop()
	op.Defer(g.Ops, m.Stop())
	return d
}

func sourceGroup(id string) string {
	kind, _, _ := strings.Cut(id, ":")
	switch {
	case strings.HasSuffix(kind, "-monitor"):
		return "输出设备 · 系统播放"
	case strings.HasSuffix(kind, "-mic"):
		return "输入设备 · 麦克风"
	case strings.HasSuffix(kind, "-app"):
		return "应用音频 · 单个程序"
	}
	return ""
}
func (u *workbench) popupBody(g C, items []menuItem, selected string, search bool) D {
	query := ""
	if search {
		query = strings.ToLower(u.edit("popup-search").Text())
	}
	if query != u.popup.query {
		u.popup.query = query
		u.popup.index = 0
		u.list("popup").ScrollTo(0)
	}
	visible := make([]menuItem, 0, len(items))
	for _, o := range items {
		if strings.Contains(strings.ToLower(o.label+" "+sourceGroup(o.id)), query) {
			visible = append(visible, o)
		}
	}
	if len(visible) > 0 {
		u.popup.index = min(u.popup.index, len(visible)-1)
	}
	for {
		ev, ok := g.Event(key.Filter{Name: key.NameUpArrow}, key.Filter{Name: key.NameDownArrow}, key.Filter{Name: key.NameReturn}, key.Filter{Name: "S", Required: key.ModShortcut})
		if !ok {
			break
		}
		k, ok := ev.(key.Event)
		if !ok || k.State != key.Press || len(visible) == 0 {
			continue
		}
		switch k.Name {
		case key.NameUpArrow:
			u.popup.index = max(0, u.popup.index-1)
		case key.NameDownArrow:
			u.popup.index = min(len(visible)-1, u.popup.index+1)
		default:
			act := visible[u.popup.index].act
			u.closePopup()
			act()
			return D{}
		}
		u.list("popup").ScrollTo(u.popup.index)
	}
	return widget.Border{Color: line, Width: 1, CornerRadius: 0}.Layout(g, func(g C) D {
		return box(g, paper, 4, inset(4, func(g C) D {
			parts := []layout.FlexChild{}
			if search {
				parts = append(parts, layout.Rigid(u.field("popup-search", "筛选选项", false, false)))
			}
			if len(visible) == 0 {
				parts = append(parts, layout.Rigid(inset(8, u.text("没有匹配项", 13, muted, false))))
			} else {
				parts = append(parts, layout.Rigid(func(g C) D {
					return material.List(u.th, u.list("popup")).Layout(g, len(visible), func(g C, i int) D {
						o := visible[i]
						btn := u.click("option/" + u.popup.id + "/" + o.id)
						for btn.Clicked(g) {
							u.closePopup()
							o.act()
						}
						rowWidget := func(g C) D {
							return btn.Layout(g, func(g C) D {
								bg := paper
								if btn.Hovered() || i == u.popup.index {
									bg = hover
								}
								check := ""
								if o.id == selected {
									check = "✓"
								}
								return box(full(g), bg, 3, padding{Top: 7, Bottom: 7, Left: 6, Right: 8}.Wrap(func(g C) D {
									return (layout.Flex{Alignment: layout.Middle}).Layout(g, layout.Rigid(fixedWidth(17, u.oneLine(check, 13, selection))), layout.Flexed(1, u.oneLine(o.label, 14, ink)), layout.Rigid(u.oneLine(o.hint, 12, muted)))
								}))
							})
						}
						group := sourceGroup(o.id)
						if group != "" && (i == 0 || sourceGroup(visible[i-1].id) != group) {
							return col(2, func(g C) D { return box(full(g), canvas, 3, inset(6, u.text(group, 12, ink, true))) }, rowWidget)(g)
						}
						return rowWidget(g)
					})
				}))
			}
			return (layout.Flex{Axis: layout.Vertical}).Layout(g, parts...)
		}))
	})
}

func (u *workbench) trackPointer(g C) {
	for {
		ev, ok := g.Event(pointer.Filter{Target: &u.pointerAt, Kinds: pointer.Press})
		if !ok {
			break
		}
		if p, ok := ev.(pointer.Event); ok {
			u.pointerAt = p.Position.Round()
		}
	}
	pass := pointer.PassOp{}.Push(g.Ops)
	area := clip.Rect(image.Rectangle{Max: g.Constraints.Max}).Push(g.Ops)
	event.Op(g.Ops, &u.pointerAt)
	area.Pop()
	pass.Pop()
}

func fixedWidth(w unit.Dp, child W) W {
	return func(g C) D {
		v := min(g.Dp(w), g.Constraints.Max.X)
		g.Constraints.Min.X = v
		g.Constraints.Max.X = v
		return child(g)
	}
}
