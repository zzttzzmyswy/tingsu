package media

import (
	"bytes"
	"context"
	"encoding/base64"
	"image"
	"image/jpeg"
	"io"
	"os"

	"golang.org/x/image/draw"
)

// JPEG fits a frame within maxWidth and optionally maxHeight, preserving its aspect ratio.
func JPEG(img image.Image, maxWidth, maxHeight int) ([]byte, error) {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	if w > maxWidth {
		h = max(1, h*maxWidth/w)
		w = maxWidth
	}
	if maxHeight > 0 && h > maxHeight {
		w = max(1, w*maxHeight/h)
		h = maxHeight
	}
	if w != b.Dx() || h != b.Dy() {
		out := image.NewRGBA(image.Rect(0, 0, w, h))
		draw.ApproxBiLinear.Scale(out, out.Bounds(), img, b, draw.Src, nil)
		img = out
	}
	var buf bytes.Buffer
	err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: 89})
	return buf.Bytes(), err
}
func Thumbnail(ctx context.Context, path string, width int) string {
	if ctx.Err() != nil || width <= 0 {
		return ""
	}
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	im, err := jpeg.Decode(io.LimitReader(f, 64<<20))
	if err != nil {
		return ""
	}
	data, err := JPEG(im, width, width)
	if err != nil {
		return ""
	}
	return "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(data)
}
