package media

import (
	"context"
	"image"
	"image/color"
	"image/jpeg"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestThumbnailOfStandaloneJPEG(t *testing.T) {
	if _, e := exec.LookPath("ffmpeg"); e != nil {
		t.Skip("requires FFmpeg")
	}
	p := filepath.Join(t.TempDir(), "会议画面.jpg")
	f, e := os.Create(p)
	if e != nil {
		t.Fatal(e)
	}
	im := image.NewRGBA(image.Rect(0, 0, 64, 40))
	for i := 0; i < 30; i++ {
		im.Set(i, i, color.White)
	}
	if e = jpeg.Encode(f, im, nil); e != nil {
		t.Fatal(e)
	}
	f.Close()
	if s := Thumbnail(context.Background(), p, 48); !strings.HasPrefix(s, "data:image/jpeg;base64,") || len(s) < 200 {
		t.Fatal("empty screenshot thumbnail")
	}
}
