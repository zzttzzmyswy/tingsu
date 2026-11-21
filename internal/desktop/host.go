package desktop

import (
	"context"
	"fmt"
	"os"
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
	bridge  *bridge
	ctx     context.Context
	cancel  context.CancelFunc
	in      chan struct{}
	inboxMu sync.Mutex
	inbox   []func()
	dirty   chan struct{}
	deliver func(M)
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
	h.deliver(p)
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
				if a == "--x11" {
					// libwayland defaults to wayland-0 even when its variable is unset.
					os.Setenv("WAYLAND_DISPLAY", "/dev/null")
				} else if os.Getenv("WAYLAND_DISPLAY") == "" {
					os.Setenv("WAYLAND_DISPLAY", "wayland-0")
				}
				os.Setenv("GDK_BACKEND", a[2:])
				os.Setenv("TINGSU_CAPTURE_DESKTOP", a[2:])
			}
		case "--version":
			fmt.Println("Tingsu 2.2.0")
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
	h := &host{ctx: ctx, cancel: cancel, in: make(chan struct{}, 1), dirty: make(chan struct{}, 1), dialogs: map[int]func(any){}}
	ui := newWorkbench(func(p packet) { h.post(func() { h.dispatch(p) }) })
	h.deliver = ui.receive
	if acquired {
		h.bridge, e = newBridge(h, data)
		if e != nil {
			return e
		}
		defer h.bridge.close()
	}
	// Background callbacks and commands run on a single owner goroutine.
	done := make(chan struct{})
	go func() { defer close(done); h.serve() }()
	err := ui.run()
	cancel()
	<-done
	return err
}

func (h *host) serve() {
	ctx, b := h.ctx, h.bridge
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
		}
	}
}

func (h *host) dispatch(p packet) {
	b := h.bridge
	switch p.Type {
	case "ready":
		if b == nil {
			h.dialog("message", "听序", M{"message": "同一个本地工作空间已经打开。"}, func(any) { h.quit() })
		} else {
			b.ready = true
			b.publish()
			b.loadSources()

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
