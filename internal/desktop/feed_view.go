package desktop

import (
	"fmt"
	"image"
	"math"
	"sort"

	"gioui.org/io/event"
	"gioui.org/io/pointer"
	"gioui.org/layout"
	"gioui.org/op"
	"gioui.org/op/clip"
	"gioui.org/op/paint"
)

// The list owns wheel/touch scrolling. The thumb uses the same first row and
// pixel offset; a press or drag maps an absolute track position to those values.
type recordView struct {
	list    layout.List
	rows    []M
	ids     []string
	heights map[string]int
	offsets []int
	bar     thumbDrag
	stream  bool
}
type thumbDrag struct {
	active            bool
	settleFrames      int
	id                pointer.ID
	grip, y, fraction float32
	length            int
}

func (v *recordView) replace(events []M, kind string, older, stream bool) {
	pos := &v.list.Position
	var anchor string
	if pos.BeforeEnd && pos.First >= 0 && pos.First < len(v.ids) {
		anchor = v.ids[pos.First]
	}
	// Snapshots normally contain the newest 120 records. Keep already
	// displayed history while someone is reading it, even if new speech
	// has moved that record outside the server's current snapshot.
	if pos.BeforeEnd && older && len(events) > 0 {
		var prior []M
		at, seq := number(events[0]["at"]), number(events[0]["seq"])
		for _, e := range v.rows {
			if e == nil {
				continue
			}
			t := number(e["at"])
			if t < at || (t == at && number(e["seq"]) < seq) {
				prior = append(prior, e)
			}
		}
		if len(prior) > 0 {
			events = append(prior, events...)
		}
	}
	v.rows, v.ids = v.rows[:0], v.ids[:0]
	if older {
		v.rows = append(v.rows, nil)
		v.ids = append(v.ids, "older")
	}
	for _, e := range events {
		if kind != "" && kind != str(e["kind"]) {
			continue
		}
		v.ids = append(v.ids, fmt.Sprint(e["seq"]))
		v.rows = append(v.rows, e)
	}
	v.stream = stream && (kind == "" || kind == "assistant")
	if v.stream {
		v.rows = append(v.rows, nil)
		v.ids = append(v.ids, "stream")
	}
	if anchor != "" {
		for i, id := range v.ids {
			if id == anchor {
				pos.First = i
				return
			}
		}
		// A streaming placeholder can be replaced by the persisted reply in
		// this snapshot. Preserve its reading offset in the replacement.
		pos.First = max(0, min(pos.First, len(v.ids)-1))
		if anchor == "stream" {
			for i := len(v.rows) - 1; i >= 0; i-- {
				if str(v.rows[i]["kind"]) == "assistant" {
					pos.First = i
					break
				}
			}
		}
	}
}

func (u *workbench) updateFeed() {
	u.records.replace(rows(u.state["events"]), u.feedKind, truth(u.state["older"]), u.stream != "")
}

func (v *recordView) measure(defaultHeight int) {
	v.offsets = append(v.offsets[:0], 0)
	total := 0
	for _, id := range v.ids {
		h := v.heights[id]
		if h == 0 {
			h = defaultHeight
		}
		total += h
		v.offsets = append(v.offsets, total)
	}
}
func (v *recordView) rangePx(height int) int {
	return max(0, v.offsets[len(v.offsets)-1]-height)
}
func (v *recordView) fraction(height int) float32 {
	pos := v.list.Position
	if !pos.BeforeEnd {
		return 1
	}
	if extent := v.rangePx(height); extent > 0 {
		offset := v.offsets[min(pos.First, len(v.rows))] + pos.Offset
		return float32(max(0, min(offset, extent))) / float32(extent)
	}
	return 0
}
func (v *recordView) seek(fraction float32, height int) {
	if fraction >= 1 {
		v.list.Position.BeforeEnd = false
		return
	}
	y := int(math.Round(float64(fraction) * float64(v.rangePx(height))))
	i := sort.Search(len(v.rows), func(i int) bool { return v.offsets[i+1] > y })
	v.list.Position.First, v.list.Position.Offset = i, y-v.offsets[i]
	v.list.Position.BeforeEnd = true
}
func (v *recordView) thumbLength(g C, h int) int {
	content := h + v.rangePx(h)
	if content <= h {
		return h
	}
	return min(h, max(g.Dp(20), int(float64(h)*float64(h)/float64(content))))
}

func (v *recordView) input(g C, height int) {
	b := &v.bar
	for {
		ev, ok := g.Event(pointer.Filter{Target: b, Kinds: pointer.Press | pointer.Drag | pointer.Release | pointer.Cancel})
		if !ok {
			break
		}
		e := ev.(pointer.Event)
		switch e.Kind {
		case pointer.Press:
			if b.active || (e.Source == pointer.Mouse && e.Buttons&pointer.ButtonPrimary == 0) {
				continue
			}
			b.length = v.thumbLength(g, height)
			if b.length >= height {
				continue
			}
			b.active, b.id = true, e.PointerID
			b.fraction = v.fraction(height)
			b.grip = (e.Position.Y - b.fraction*float32(height-b.length)) / float32(b.length)
			if b.grip < 0 || b.grip > 1 {
				b.grip = .5
			}
			g.Execute(pointer.GrabCmd{Tag: b, ID: b.id})
		case pointer.Drag, pointer.Release:
			if !b.active || e.PointerID != b.id {
				continue
			}
		case pointer.Cancel:
			if e.PointerID == b.id {
				b.active, b.settleFrames = false, 0
			}
			continue
		}
		b.y = e.Position.Y
		if e.Kind == pointer.Release {
			b.active = false
			b.settleFrames = 4
		}
	}
	if b.active || b.settleFrames > 0 {
		if !b.active {
			b.length = v.thumbLength(g, height)
		}
		b.fraction = b.target(height, b.length)
		v.seek(b.fraction, height)
	}
}
func (b *thumbDrag) target(height, length int) float32 {
	return max(0, min(1, (b.y-b.grip*float32(length))/float32(max(1, height-length))))
}

func (u *workbench) recordList(g C) D {
	v := &u.records
	if len(v.rows) == 0 {
		return layout.Center.Layout(g, u.text("会议发言与 AI 回复将显示在这里", 14, muted, false))
	}
	v.list.Axis, v.list.ScrollToEnd = layout.Vertical, true
	if v.heights == nil {
		v.heights = make(map[string]int)
	}
	w, h := g.Constraints.Max.X, g.Constraints.Max.Y
	barWidth := min(w, g.Dp(14))
	v.measure(g.Dp(68))
	v.input(g, h)
	content := g
	content.Constraints = layout.Exact(image.Pt(w-barWidth, h))
	u.textPane(content, &u.feedText, func(content C) D {
		return v.list.Layout(content, len(v.rows), func(g C, i int) D {
			id := v.ids[i]
			var d D
			switch id {
			case "older":
				d = u.button("older", "载入更早记录", false, func() { u.cmd("older", nil) })(g)
			case "stream":
				d = u.eventRow(g, M{"kind": "assistant", "speaker": "听序 AI", "text": u.stream, "seq": "stream"})
			default:
				d = u.eventRow(g, v.rows[i])
			}
			v.heights[id] = max(1, d.Size.Y)
			return d
		})
	})
	v.measure(g.Dp(68))
	if v.rangePx(h) == 0 {
		v.bar = thumbDrag{}
	} else {
		length, fraction := v.thumbLength(g, h), v.fraction(h)
		if v.bar.settleFrames > 0 {
			v.bar.settleFrames--
			// Visible rows may just have supplied more accurate heights. Settle
			// on the released pixel once those measurements are available.
			want := v.bar.target(h, length)
			if math.Abs(float64((want-fraction)*float32(h-length))) > 0.75 {
				g.Execute(op.InvalidateCmd{})
			} else {
				v.bar.settleFrames = 0
			}
		}
		if v.bar.active {
			length, fraction = min(h, v.bar.length), v.bar.fraction
		}
		y := int(math.Round(float64(fraction) * float64(h-length)))
		area := clip.Rect(image.Rect(w-barWidth, 0, w, h)).Push(g.Ops)
		paint.Fill(g.Ops, canvas)
		event.Op(g.Ops, &v.bar)
		paint.FillShape(g.Ops, muted, clip.Rect(image.Rect(w-barWidth+g.Dp(3), y, w-g.Dp(3), y+length)).Op())
		area.Pop()
	}
	return D{Size: g.Constraints.Max}
}
