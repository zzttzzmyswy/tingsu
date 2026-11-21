package desktop

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"time"
)

type packet struct {
	Type  string
	Name  string
	Args  M
	ID    int
	Value any
}
type host struct {
	ctx     context.Context
	cancel  context.CancelFunc
	in      chan struct{}
	inboxMu sync.Mutex
	inbox   []func()
	dirty   chan struct{}
	out     *json.Encoder
	dialogs map[int]func(any)
	serial  int
	closing bool
}

// Posting never waits for the UI, including while a worker holds its cancellation lock.
func (h *host) post(f func()) {
	if h.ctx.Err() != nil {
		return
	}
	h.inboxMu.Lock()
	h.inbox = append(h.inbox, f)
	h.inboxMu.Unlock()
	select {
	case h.in <- struct{}{}:
	default:
	}
}
func (h *host) drain() {
	h.inboxMu.Lock()
	batch := h.inbox
	h.inbox = nil
	h.inboxMu.Unlock()
	for _, f := range batch {
		f()
	}
}
func (h *host) changed() {
	select {
	case h.dirty <- struct{}{}:
	default:
	}
}
func (h *host) write(p M) {
	if err := h.out.Encode(p); err != nil {
		h.cancel()
	}
}
func (h *host) signal(name string, args ...any) {
	h.write(M{"type": "signal", "name": name, "args": args})
}
func (h *host) dialog(kind, title string, fields M, fn func(any)) {
	h.serial++
	h.dialogs[h.serial] = fn
	p := M{"type": "dialog", "id": h.serial, "kind": kind, "title": title}
	for k, v := range fields {
		p[k] = v
	}
	h.write(p)
}
func (h *host) quit() { h.write(M{"type": "quit"}); h.cancel() }

func Run(args []string) error {
	for _, a := range args {
		switch a {
		case "--x11", "--wayland":
			if runtime.GOOS == "linux" {
				os.Setenv("GDK_BACKEND", a[2:])
				os.Setenv("TINGSU_CAPTURE_DESKTOP", a[2:])
			}
		case "--version":
			fmt.Println("Tingsu 2.0.1")
			return nil
		default:
			return fmt.Errorf("unknown argument %s", a)
		}
	}
	if runtime.GOOS == "linux" && os.Getenv("GDK_BACKEND") == "" && os.Getenv("WAYLAND_DISPLAY") != "" {
		os.Setenv("GDK_BACKEND", "wayland,x11")
	}
	data := appDir("data")
	if e := os.MkdirAll(data, 0700); e != nil {
		return e
	}
	lock, e := os.OpenFile(filepath.Join(data, "desktop.instance"), os.O_RDWR|os.O_CREATE, 0600)
	if e != nil {
		return e
	}
	defer lock.Close()
	acquired := lockProfile(lock) == nil
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var b *bridge
	name := os.Getenv("TINGSU_DESKTOP_HOST")
	if name == "" {
		name = "tingsu-webview"
		if runtime.GOOS == "windows" {
			name += ".exe"
		}
	}
	base := os.Getenv("TINGSU_BUNDLE")
	if base == "" {
		base = "meeting_agent"
	}
	ui, e := filepath.Abs(filepath.Join(base, "ui", "index.html"))
	if e != nil {
		return e
	}
	if _, e = os.Stat(ui); e != nil {
		return e
	}
	c := exec.Command(name, ui)
	c.Stderr = os.Stderr
	read, e := c.StdoutPipe()
	if e != nil {
		return e
	}
	write, e := c.StdinPipe()
	if e != nil {
		return e
	}
	h := &host{ctx: ctx, cancel: cancel, in: make(chan struct{}, 1), dirty: make(chan struct{}, 1), out: json.NewEncoder(write), dialogs: map[int]func(any){}}
	if acquired {
		if b, e = newBridge(h, data); e != nil {
			return e
		}
		defer b.close()
	}
	if e = c.Start(); e != nil {
		return fmt.Errorf("无法启动界面组件 %s: %w", name, e)
	}
	input := make(chan packet, 16)
	go readPackets(ctx, read, input)
	tick := time.NewTicker(350 * time.Millisecond)
	defer tick.Stop()
	var refresh <-chan time.Time
	for ctx.Err() == nil {
		select {
		case <-ctx.Done():
		case <-h.in:
			h.drain()
		case <-h.dirty:
			if refresh == nil {
				refresh = time.After(40 * time.Millisecond)
			}
		case <-refresh:
			refresh = nil
			if b != nil && b.ready {
				b.publish()
			}
		case <-tick.C:
			if b != nil && b.ready && b.asr.Status() == "recording" {
				if t, ok := b.asr.Position(); ok {
					b.event("position", fmt.Sprint(t))
				}
			}
		case p, ok := <-input:
			if !ok {
				cancel()
				break
			}
			switch p.Type {
			case "ready":
				if b == nil {
					h.dialog("message", "听序", M{"message": "同一个本地工作空间已经打开。"}, func(any) { h.quit() })
				} else {
					b.ready = true
					b.publish()
					b.loadSources()
					b.loadLibrary()
				}
			case "dialog-result":
				if fn := h.dialogs[p.ID]; fn != nil {
					delete(h.dialogs, p.ID)
					fn(p.Value)
				}
			case "command":
				if b != nil {
					if err := b.command(p.Name, p.Args); err != nil {
						b.fail(err)
					}
				}
			case "close-request":
				if b != nil && b.active() {
					if !h.closing {
						h.closing = true
						h.dialog("confirm", "退出听序", M{"message": "退出会结束当前采集和 AI 回复。是否退出？"}, func(v any) {
							h.closing = false
							if v == true {
								h.quit()
							}
						})
					}
				} else {
					h.quit()
				}
			}
		}
	}
	write.Close()
	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case err := <-done:
		if err != nil && !errors.Is(err, io.EOF) {
			return err
		}
	case <-time.After(10 * time.Second):
		c.Process.Kill()
		<-done
	}
	return nil
}
func readPackets(ctx context.Context, r io.Reader, sink chan<- packet) {
	defer close(sink)
	s := bufio.NewScanner(r)
	s.Buffer(make([]byte, 16384), 16<<20)
	for s.Scan() {
		var p packet
		if json.Unmarshal(s.Bytes(), &p) == nil {
			select {
			case sink <- p:
			case <-ctx.Done():
				return
			}
		}
	}
}
