package desktop

import (
	"image"
	"math"

	"gioui.org/layout"
	"gioui.org/op/clip"
	"gioui.org/op/paint"
)

// The meter consumes peak samples directly from capture, independent of ASR latency.
func (u *workbench) meter(g C) D {
	active := str(u.state["capture"]) == "recording"
	label := "音量"
	illuminated := 0
	if active {
		label = "等待输入"
		if u.receiving {
			if u.peak > 0 {
				illuminated = int(math.Ceil(10 * max(0., 1+20*math.Log10(u.peak)/60)))
			}
			label = "静音"
			if illuminated > 0 {
				label = "收音中"
			}
		}
	}
	return padding{Left: 4, Right: 8}.Wrap(func(g C) D {
		return (layout.Flex{Axis: layout.Vertical}).Layout(g, layout.Rigid(u.oneLine(label, 10, muted)), layout.Rigid(func(g C) D {
			w, h := g.Dp(44), g.Dp(7)
			for i := 0; i < 10; i++ {
				c := rgb(0xc9d6d2)
				if i < illuminated {
					c = teal
					if i >= 8 {
						c = rgb(0xc77c24)
					}
				}
				paint.FillShape(g.Ops, c, clip.Rect(image.Rect(i*w/10, 0, (i+1)*w/10-1, h)).Op())
			}
			return D{Size: image.Pt(w, h)}
		}))
	})(g)
}
