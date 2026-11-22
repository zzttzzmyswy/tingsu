package desktop

import (
	"image/color"

	"gioui.org/font"
	"gioui.org/unit"
)

// Gio draws the controls; all surfaces, selection and text use desktop roles.
type appearance struct {
	Text, Window, Panel, Button, ButtonText, Border, Highlight, HighlightText color.NRGBA
	Family                                                                    string
	TextPx                                                                    float32
}

func defaultAppearance(dark bool) appearance {
	if dark {
		return appearance{rgb(0xeaf0f7), rgb(0x222932), rgb(0x191e26), rgb(0x303944), rgb(0xeaf0f7), rgb(0x647789), rgb(0x67b6ff), rgb(0x091440), "sans-serif", 14}
	}
	return appearance{rgb(0x091440), rgb(0xffffff), rgb(0xf3f5f6), rgb(0xffffff), rgb(0x091440), rgb(0xb8c5d1), rgb(0x245edb), rgb(0xffffff), "sans-serif", 14}
}

func mix(a, b color.NRGBA, p int) color.NRGBA {
	ch := func(x, y uint8) uint8 { return uint8((int(x)*(100-p) + int(y)*p) / 100) }
	return color.NRGBA{R: ch(a.R, b.R), G: ch(a.G, b.G), B: ch(a.B, b.B), A: 255}
}

var desktopColors = defaultAppearance(false)
var ink, paper, canvas, line, muted, selection, hover, accentInk color.NRGBA

func (u *workbench) applyAppearance(a appearance) {
	desktopColors = a
	clear(u.markdownRows)
	ink, paper, canvas, line, selection = a.Text, a.Window, a.Panel, a.Border, a.Highlight
	muted, hover = mix(a.Text, a.Window, 22), mix(a.Window, a.Highlight, 18)
	accentInk = mix(ink, selection, 45)
	u.th.Fg, u.th.Bg, u.th.ContrastBg, u.th.ContrastFg = ink, paper, selection, a.HighlightText
	u.th.Face = font.Typeface(a.Family)
	u.th.TextSize = unit.Sp(a.TextPx)
}
func (u *workbench) fontSize(size unit.Sp) unit.Sp { return size * u.th.TextSize / 14 }

func onHighlight(c color.NRGBA) color.NRGBA {
	if int(c.R)*299+int(c.G)*587+int(c.B)*114 > 150000 {
		return rgb(0)
	}
	return rgb(0xffffff)
}
