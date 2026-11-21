package media

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image/jpeg"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type Row = map[string]any

func Supported(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".mkv", ".webm", ".mp4", ".mov", ".avi", ".m4v", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus":
		return true
	}
	return false
}
func jpegFrame(ctx context.Context, path string, at float64, width int) ([]byte, error) {
	child, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return command(child, "ffmpeg", "-nostdin", "-loglevel", "error", "-ss", strconv.FormatFloat(at, 'f', -1, 64), "-i", path, "-map", "0:v:0", "-frames:v", "1", "-filter_threads", "1", "-threads", "1", "-vf", fmt.Sprintf("scale=%d:%d:force_original_aspect_ratio=decrease", width, width), "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1").Output()
}
func Thumbnail(ctx context.Context, path string, width int) string {
	b, e := jpegFrame(ctx, path, 0, width)
	if e != nil || len(b) == 0 {
		return ""
	}
	return "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(b)
}
func hasPicture(data []byte) bool {
	im, e := jpeg.Decode(bytes.NewReader(data))
	if e != nil {
		return false
	}
	count := 0
	b := im.Bounds()
	for y := 0; y < 9; y++ {
		for x := 0; x < 16; x++ {
			r, g, v, _ := im.At(b.Min.X+x*b.Dx()/16, b.Min.Y+y*b.Dy()/9).RGBA()
			if (max(r, g, v)+min(r, g, v))>>8 > 50 {
				count++
			}
		}
	}
	return count > 12
}

var trimTitle = regexp.MustCompile(`^\d{14}-|[-－]视频-\d+-共享屏幕$`)

func Describe(ctx context.Context, path, cacheDir string) (Row, error) {
	path, e := filepath.Abs(path)
	if e != nil {
		return nil, e
	}
	st, e := os.Stat(path)
	if e != nil {
		return nil, e
	}
	h := sha256.Sum256([]byte(fmt.Sprintf("3:%s:%d:%d", path, st.Size(), st.ModTime().UnixNano())))
	id := fmt.Sprintf("%x", h[:12])
	dir := filepath.Join(cacheDir, "library")
	cache := filepath.Join(dir, id+".json")
	if b, e := os.ReadFile(cache); e == nil {
		var r Row
		if json.Unmarshal(b, &r) == nil && r["id"] == id {
			return r, nil
		}
	}
	child, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	b, e := command(child, "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", path).Output()
	if e != nil {
		return nil, e
	}
	var probe struct {
		Format  struct{ Duration string }
		Streams []struct {
			Kind  string `json:"codec_type"`
			Width int
		}
	}
	if e = json.Unmarshal(b, &probe); e != nil {
		return nil, e
	}
	duration, _ := strconv.ParseFloat(probe.Format.Duration, 64)
	base := filepath.Base(path)
	title := trimTitle.ReplaceAllString(strings.TrimSuffix(base, filepath.Ext(base)), "")
	r := Row{"id": id, "title": title, "duration": duration, "path": path, "filename": base, "width": nil, "size": int(math.Round(float64(st.Size()) / 1048576)), "image": ""}
	for _, s := range probe.Streams {
		if s.Kind != "video" {
			continue
		}
		r["width"] = s.Width
		for _, f := range []float64{.12, .38, .65, 0} {
			p, e := jpegFrame(ctx, path, f*duration, 480)
			if e == nil && len(p) > 0 {
				r["image"] = "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(p)
				if hasPicture(p) {
					break
				}
			}
		}
		break
	}
	if e := os.MkdirAll(dir, 0700); e == nil {
		if f, e := os.CreateTemp(dir, id+"-*"); e == nil {
			name := f.Name()
			defer os.Remove(name)
			e = json.NewEncoder(f).Encode(r)
			closeErr := f.Close()
			if e == nil && closeErr == nil {
				os.Rename(name, cache)
			}
		}
	}
	return r, nil
}
func Frame(ctx context.Context, video string, at float64, output string) error {
	if math.IsNaN(at) || math.IsInf(at, 0) || at < 0 {
		return errors.New("画面时间必须是非负数")
	}
	if e := os.MkdirAll(filepath.Dir(output), 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(output), ".frame-*")
	if e != nil {
		return e
	}
	tmp := f.Name()
	f.Close()
	defer os.Remove(tmp)
	codec := "mjpeg"
	if strings.EqualFold(filepath.Ext(output), ".png") {
		codec = "png"
	}
	for _, tail := range []bool{false, true} {
		child, cancel := context.WithTimeout(ctx, 25*time.Second)
		a := []string{"-nostdin", "-loglevel", "error", "-y"}
		if tail {
			a = append(a, "-noaccurate_seek")
		}
		a = append(a, "-ss", strconv.FormatFloat(at, 'f', -1, 64), "-i", video, "-map", "0:v:0")
		vf := "scale='min(iw,1920)':-2"
		if tail {
			vf = "setpts=PTS-STARTPTS," + vf
		} else {
			a = append(a, "-frames:v", "1")
		}
		a = append(a, "-filter_threads", "1", "-threads", "1", "-vf", vf, "-c:v", codec, "-update", "1", "-f", "image2", tmp)
		err := command(child, "ffmpeg", a...).Run()
		cancel()
		if err == nil {
			if st, e := os.Stat(tmp); e == nil && st.Size() > 0 {
				return os.Rename(tmp, output)
			}
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if e := os.Truncate(tmp, 0); e != nil {
			return e
		}
	}
	return errors.New("这个时间点没有可读取的画面")
}
