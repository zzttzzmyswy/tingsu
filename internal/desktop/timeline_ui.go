package desktop

import (
	"image"
	"math"

	"gioui.org/gesture"
	"gioui.org/layout"
	"gioui.org/op/clip"
	"gioui.org/op/paint"
)

// Draw the stored audio peaks. Clicking the track jumps to that time in the transcript.
func (u *workbench) timeline(g C) D {
	levels, _ := u.state["levels"].([]any)
	if len(rows(u.state["trackSpeech"])) == 0 && len(levels) == 0 {
		return D{}
	}
	start, end := number(u.meeting()["origin"]), max(u.position, number(u.meeting()["duration"]))
	if end <= start {
		return D{}
	}
	btn := &u.timelineClick
	for {
		ev, ok := btn.Update(g.Source)
		if !ok {
			break
		}
		if ev.Kind != gesture.KindClick {
			continue
		}
		at := start + (end-start)*float64(ev.Position.X)/float64(max(1, g.Constraints.Max.X))
		u.cmd("jump", M{"at": min(end, max(start, at))})
	}
	return layout.Inset{Top: 6, Bottom: 6}.Layout(g, func(g C) D {
		w, h := g.Constraints.Max.X, g.Dp(19)
		area := clip.Rect(image.Rect(0, 0, w, h)).Push(g.Ops)
		defer area.Pop()
		btn.Add(g.Ops)
		paint.FillShape(g.Ops, mint, clip.UniformRRect(image.Rect(0, 0, w, h), g.Dp(4)).Op(g.Ops))
		if len(levels) == 0 {
			for _, speech := range rows(u.state["trackSpeech"]) {
				x := int((number(speech["at"]) - start) * float64(w) / (end - start))
				right := int((number(speech["end"]) - start) * float64(w) / (end - start))
				paint.FillShape(g.Ops, teal, clip.Rect(image.Rect(x, h/3, max(x+1, right), 2*h/3)).Op())
			}
		}
		for i, v := range levels {
			x := i * w / len(levels)
			right := (i + 1) * w / len(levels)
			a := math.Min(1, math.Max(0, number(v)))
			height := max(1, int(float64(h)*a))
			paint.FillShape(g.Ops, teal, clip.Rect(image.Rect(x, (h-height)/2, max(x+1, right-1), (h+height)/2+1)).Op())
		}
		return D{Size: image.Pt(w, h)}
	})
}
