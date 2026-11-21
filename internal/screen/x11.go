package screen

import (
	"context"
	"errors"
	"fmt"
	"image"
	"math/bits"
	"strconv"

	"github.com/jezek/xgb"
	"github.com/jezek/xgb/composite"
	"github.com/jezek/xgb/xproto"
	"github.com/zzttzzmyswy/tingsu/internal/media"
)

// Keep the window redirected for the lifetime of the selection. Reading its
// backing pixmap avoids capturing a different window covering the player.
func openX11(id string) (*xgb.Conn, xproto.Window, error) {
	n, err := strconv.ParseUint(id, 0, 32)
	if err != nil || n == 0 {
		return nil, 0, errors.New("无效的 X11 窗口")
	}
	w := xproto.Window(n)
	c, err := xgb.NewConn()
	if err != nil {
		return nil, 0, err
	}
	if err = composite.Init(c); err == nil {
		_, err = composite.QueryVersion(c, 0, 4).Reply()
	}
	if err == nil {
		err = composite.RedirectWindowChecked(c, w, composite.RedirectAutomatic).Check()
	}
	if err != nil {
		c.Close()
		return nil, 0, fmt.Errorf("无法获取所选窗口的 XComposite 画面：%w", err)
	}
	return c, w, nil
}
func x11Frame(ctx context.Context, c *xgb.Conn, w xproto.Window) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	attr, err := xproto.GetWindowAttributes(c, w).Reply()
	if err != nil {
		return nil, err
	}
	if attr.MapState != xproto.MapStateViewable {
		return nil, errors.New("所选窗口不可见")
	}
	pix, err := xproto.NewPixmapId(c)
	if err != nil {
		return nil, err
	}
	if err = composite.NameWindowPixmapChecked(c, w, pix).Check(); err != nil {
		return nil, err
	}
	defer xproto.FreePixmap(c, pix)
	d := xproto.Drawable(pix)
	geom, err := xproto.GetGeometry(c, d).Reply()
	if err != nil {
		return nil, err
	}
	width, height := int(geom.Width), int(geom.Height)
	if width < 1 || height < 1 || uint64(width)*uint64(height) > 32<<20 {
		return nil, errors.New("X11 窗口尺寸无效")
	}
	setup := xproto.Setup(c)
	var format xproto.Format
	for _, f := range setup.PixmapFormats {
		if f.Depth == geom.Depth {
			format = f
			break
		}
	}
	var visual xproto.VisualInfo
	for _, depth := range setup.DefaultScreen(c).AllowedDepths {
		for _, v := range depth.Visuals {
			if v.VisualId == attr.Visual {
				visual = v
			}
		}
	}
	size, pad := int(format.BitsPerPixel)/8, int(format.ScanlinePad)/8
	if size < 2 || size > 4 || pad == 0 || visual.RedMask == 0 || visual.GreenMask == 0 || visual.BlueMask == 0 {
		return nil, errors.New("不支持此 X11 像素格式")
	}
	shot, err := xproto.GetImage(c, xproto.ImageFormatZPixmap, d, 0, 0, geom.Width, geom.Height, 0xffffffff).Reply()
	if err != nil {
		return nil, err
	}
	stride := (width*size + pad - 1) / pad * pad
	if len(shot.Data) < stride*height {
		return nil, errors.New("X11 窗口画面不完整")
	}
	im := image.NewRGBA(image.Rect(0, 0, width, height))
	component := func(v, mask uint32) byte {
		shift := bits.TrailingZeros32(mask)
		return byte(((v & mask) >> shift) * 255 / (mask >> shift))
	}
	for y := 0; y < height; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < width; x++ {
			idx := y*stride + x*size
			var v uint32
			for i := 0; i < size; i++ {
				n := i
				if setup.ImageByteOrder == xproto.ImageOrderMSBFirst {
					n = size - i - 1
				}
				v |= uint32(shot.Data[idx+i]) << uint(8*n)
			}
			o := y*im.Stride + 4*x
			im.Pix[o] = component(v, visual.RedMask)
			im.Pix[o+1] = component(v, visual.GreenMask)
			im.Pix[o+2] = component(v, visual.BlueMask)
			im.Pix[o+3] = 255
		}
	}
	return media.JPEG(im, 1920, 0)
}
