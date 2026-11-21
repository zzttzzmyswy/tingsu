// Package screen acquires images from a selected desktop window or meeting recording.
package screen

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func Desktop() string {
	if runtime.GOOS == "windows" {
		return "windows"
	}
	if v := os.Getenv("TINGSU_CAPTURE_DESKTOP"); v != "" {
		return v
	}
	if v := os.Getenv("XDG_SESSION_TYPE"); v == "wayland" || v == "x11" {
		return v
	}
	if os.Getenv("WAYLAND_DISPLAY") != "" {
		return "wayland"
	}
	return "x11"
}

type Window struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	App   string `json:"app"`
}

var xwindowLine = regexp.MustCompile(`^\s*(0x[0-9a-fA-F]+) "(.*)": \("[^"]*" "([^"]+)"\)\s+(\d+)x(\d+)`)
var xwindowID = regexp.MustCompile(`Window id:\s*(0x[0-9a-fA-F]+)`)

func command(ctx context.Context, name string, args ...string) *exec.Cmd {
	c := exec.CommandContext(ctx, name, args...)
	hideWindow(c)
	return c
}
func output(c *exec.Cmd) ([]byte, error) {
	out, err := c.Output()
	var e *exec.ExitError
	if errors.As(err, &e) && len(e.Stderr) > 0 {
		return nil, fmt.Errorf("%s: %s", filepath.Base(c.Path), strings.TrimSpace(string(e.Stderr)))
	}
	return out, err
}

func Windows(ctx context.Context) ([]Window, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	windows := []Window{}
	if runtime.GOOS == "windows" {
		b, err := output(command(ctx, "tingsu-capture", "windows"))
		if err != nil {
			return nil, err
		}
		s := bufio.NewScanner(bytes.NewReader(b))
		for s.Scan() {
			var row struct{ ID, Label string }
			if err = json.Unmarshal(s.Bytes(), &row); err != nil {
				return nil, err
			}
			v := strings.Split(row.ID, "/")
			if len(v) == 3 && v[1] != strconv.Itoa(os.Getpid()) {
				windows = append(windows, Window{row.ID, row.Label, v[0]})
			}
		}
		return windows, s.Err()
	}
	if os.Getenv("DISPLAY") == "" {
		return nil, errors.New("没有可用的 X11 显示连接")
	}
	cmd := command(ctx, "xwininfo", "-root", "-tree")
	cmd.Env = append(os.Environ(), "LC_ALL=C.UTF-8")
	b, err := output(cmd)
	if err != nil {
		return nil, err
	}
	for _, line := range strings.Split(string(b), "\n") {
		m := xwindowLine.FindStringSubmatch(line)
		if len(m) == 0 || m[2] == "" {
			continue
		}
		w, _ := strconv.Atoi(m[4])
		h, _ := strconv.Atoi(m[5])
		if w < 2 || h < 2 {
			continue
		}
		c := command(ctx, "xwininfo", "-id", m[1])
		c.Env = append(os.Environ(), "LC_ALL=C")
		r, err := output(c)
		if err == nil && bytes.Contains(r, []byte("Map State: IsViewable")) {
			windows = append(windows, Window{m[1], m[2], m[3]})
		}
	}
	return windows, ctx.Err()
}

type result struct {
	path string
	err  error
}
type request struct {
	ctx   context.Context
	reply chan result
}
type Session struct {
	cancel   context.CancelFunc
	requests chan request
	done     chan struct{}
}

func Start(parent context.Context, dir string, selected *Window, onFrame func(string), onError func(error)) (*Session, error) {
	if runtime.GOOS == "windows" && selected == nil {
		return nil, errors.New("请先选择要共享的 Windows 窗口")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(parent)
	s := &Session{cancel, make(chan request), make(chan struct{})}
	go func() {
		defer close(s.done)
		defer cancel()
		err := s.run(ctx, dir, selected, onFrame)
		if err != nil && ctx.Err() == nil && onError != nil {
			onError(err)
		}
	}()
	return s, nil
}
func (s *Session) Stop() { s.cancel() }
func (s *Session) Snapshot(ctx context.Context) (string, error) {
	r := request{ctx, make(chan result, 1)}
	select {
	case s.requests <- r:
	case <-s.done:
		return "", errors.New("共享已结束")
	case <-ctx.Done():
		return "", ctx.Err()
	}
	select {
	case v := <-r.reply:
		return v.path, v.err
	case <-s.done:
		return "", errors.New("共享已结束")
	case <-ctx.Done():
		return "", ctx.Err()
	}
}
func (s *Session) run(ctx context.Context, dir string, selected *Window, onFrame func(string)) error {
	var take func(context.Context) ([]byte, error)
	var events <-chan error
	if runtime.GOOS == "windows" {
		take = func(op context.Context) ([]byte, error) {
			b, err := output(command(op, "tingsu-capture", "frame", selected.ID))
			if err != nil {
				return nil, err
			}
			c := command(op, "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "image2pipe", "-c:v", "bmp", "-i", "pipe:0", "-frames:v", "1", "-filter_threads", "1", "-vf", "scale='min(1920,iw)':-1", "-threads", "1", "-c:v", "mjpeg", "-q:v", "3", "-f", "image2pipe", "pipe:1")
			c.Stdin = bytes.NewReader(b)
			return output(c)
		}
	} else if selected != nil || Desktop() == "x11" {
		var id string
		if selected != nil {
			id = selected.ID
		} else {
			c := command(ctx, "xwininfo")
			c.Env = append(os.Environ(), "LC_ALL=C")
			b, err := output(c)
			if err != nil {
				return err
			}
			m := xwindowID.FindSubmatch(b)
			if len(m) == 0 {
				return errors.New("没有选中 X11 窗口")
			}
			id = string(m[1])
		}
		take = func(op context.Context) ([]byte, error) {
			if selected != nil {
				choices, err := Windows(op)
				if err != nil {
					return nil, err
				}
				found := false
				for _, w := range choices {
					if w.ID == id && w.App == selected.App {
						found = true
						break
					}
				}
				if !found {
					return nil, errors.New("所选窗口已关闭或更换")
				}
			}
			return output(command(op, "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "x11grab", "-window_id", id, "-draw_mouse", "0", "-i", os.Getenv("DISPLAY"), "-frames:v", "1", "-vf", "scale='min(iw,1920)':-2", "-threads", "1", "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1"))
		}
	} else {
		child, stop := context.WithCancel(ctx)
		defer stop()
		c := command(child, "tingsu-portal")
		in, err := c.StdinPipe()
		if err != nil {
			return err
		}
		out, err := c.StdoutPipe()
		if err != nil {
			return err
		}
		// Wait is called only after stdout has been consumed, including process crashes.
		var diag bytes.Buffer
		c.Stderr = &diag
		if err = c.Start(); err != nil {
			return err
		}
		frames := make(chan []byte)
		failed := make(chan error, 1)
		ended := make(chan struct{})
		defer func() { in.Close(); stop(); <-ended }()
		go func() {
			defer close(ended)
			err := readFrames(child, out, frames)
			stop()
			waitErr := c.Wait()
			if waitErr != nil && diag.Len() > 0 {
				err = errors.New(strings.TrimSpace(diag.String()))
			}
			failed <- err
		}()
		events = failed
		take = func(op context.Context) ([]byte, error) {
			if _, err := io.WriteString(in, "frame\n"); err != nil {
				return nil, err
			}
			select {
			case b := <-frames:
				return b, nil
			case e := <-failed:
				return nil, e
			case <-op.Done():
				return nil, op.Err()
			}
		}
	}
	capture := func(op context.Context) (string, error) {
		b, err := take(op)
		if err != nil {
			return "", err
		}
		if len(b) < 4 || b[0] != 0xff || b[1] != 0xd8 {
			return "", errors.New("窗口没有返回 JPEG 画面")
		}
		f, err := os.CreateTemp(dir, "screen-*.jpg")
		if err != nil {
			return "", err
		}
		_, err = f.Write(b)
		if e := f.Close(); err == nil {
			err = e
		}
		if err != nil {
			os.Remove(f.Name())
			return "", err
		}
		if onFrame != nil && ctx.Err() == nil {
			onFrame(f.Name())
		}
		return f.Name(), nil
	}
	// Portal source selection is interactive and may take longer than a frame request.
	if _, err := capture(ctx); err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-events:
			return err
		case r := <-s.requests:
			op, end := context.WithTimeout(ctx, 15*time.Second)
			unsubscribe := context.AfterFunc(r.ctx, end)
			p, e := capture(op)
			unsubscribe()
			end()
			r.reply <- result{p, e}
			if e != nil {
				return e
			}
		}
	}
}
func readFrames(ctx context.Context, r io.Reader, frames chan<- []byte) error {
	for {
		var n uint32
		if err := binary.Read(r, binary.BigEndian, &n); err != nil {
			return err
		}
		if n == 0 || n > 64<<20 {
			return errors.New("无效的共享画面")
		}
		b := make([]byte, int(n))
		if _, err := io.ReadFull(r, b); err != nil {
			return err
		}
		select {
		case frames <- b:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}
func FileFrame(ctx context.Context, source string, seconds float64, dir string) (string, error) {
	if seconds < 0 || math.IsNaN(seconds) || math.IsInf(seconds, 0) {
		return "", errors.New("无效的画面时间")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return "", err
	}
	f, err := os.CreateTemp(dir, "screen-*.jpg")
	if err != nil {
		return "", err
	}
	path := f.Name()
	if err = f.Close(); err != nil {
		os.Remove(path)
		return "", err
	}
	op, end := context.WithTimeout(ctx, 25*time.Second)
	defer end()
	for _, tail := range []bool{false, true} {
		a := []string{"-y", "-nostdin", "-loglevel", "error"}
		if tail {
			a = append(a, "-noaccurate_seek")
		}
		a = append(a, "-ss", strconv.FormatFloat(seconds, 'f', 4, 64), "-i", source, "-map", "0:v:0")
		filter := "scale='min(iw,1920)':-2"
		if tail {
			filter = "setpts=PTS-STARTPTS," + filter
		} else {
			a = append(a, "-frames:v", "1")
		}
		a = append(a, "-vf", filter, "-threads", "1", "-c:v", "mjpeg", "-update", "1", "-f", "image2", path)
		_, err = output(command(op, "ffmpeg", a...))
		if err == nil {
			if st, e := os.Stat(path); e == nil && st.Size() > 0 {
				return path, nil
			}
		}
		if op.Err() != nil {
			break
		}
	}
	os.Remove(path)
	if err == nil {
		err = errors.New("当前时间没有视频帧")
	}
	return "", err
}
