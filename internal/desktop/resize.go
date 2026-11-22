package desktop

import (
	"image"
	"time"

	"gioui.org/op"
	"gioui.org/op/clip"
)

// Keep expensive wrapped text out of the pointer's redraw path. The panes,
// controls, scrollbar and divider still use the current pointer position each
// frame. Reflow text when movement pauses and immediately on release.
type resizeContent struct {
	ops   op.Ops
	call  op.CallOp
	size  image.Point
	valid bool
}

func (u *workbench) textPane(g C, cache *resizeContent, content W) D {
	size := g.Constraints.Max
	drag := &u.boardDrag
	if u.railDrag.active {
		drag = &u.railDrag
	}
	if !cache.valid || !drag.active || (cache.size != size && g.Now.Sub(drag.lastMove) >= 45*time.Millisecond) {
		cache.ops.Reset()
		record := op.Record(&cache.ops)
		child := g
		child.Ops = &cache.ops
		content(child)
		cache.call = record.Stop()
		cache.size = size
		cache.valid = true
	}
	area := clip.Rect(image.Rectangle{Max: size}).Push(g.Ops)
	cache.call.Add(g.Ops)
	area.Pop()
	return D{Size: size}
}
