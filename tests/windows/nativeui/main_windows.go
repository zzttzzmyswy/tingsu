// Drive the packaged Go window over the Windows desktop, using real mouse and keyboard
// input, a real file picker and (when supplied) a separate VLC process.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
	_ "modernc.org/sqlite"
)

var user = windows.NewLazySystemDLL("user32.dll")

func call(name string, a ...uintptr) uintptr { r, _, _ := user.NewProc(name).Call(a...); return r }
func title(h uintptr) string {
	w := make([]uint16, 512)
	call("GetWindowTextW", h, uintptr(unsafe.Pointer(&w[0])), uintptr(len(w)))
	return windows.UTF16ToString(w)
}
func window(pid int, match string) uintptr {
	var result uintptr
	cb := windows.NewCallback(func(h, arg uintptr) uintptr {
		var p uint32
		call("GetWindowThreadProcessId", h, uintptr(unsafe.Pointer(&p)))
		if int(p) == pid && call("IsWindowVisible", h) != 0 && strings.Contains(title(h), match) {
			result = h
			return 0
		}
		return 1
	})
	call("EnumWindows", cb, 0)
	return result
}
func wait(desc string, f func() bool) error {
	until := time.Now().Add(40 * time.Second)
	for time.Now().Before(until) {
		if f() {
			return nil
		}
		time.Sleep(180 * time.Millisecond)
	}
	return fmt.Errorf("等待超时 %s", desc)
}
func focus(h uintptr) {
	call("ShowWindow", h, 9)
	fg := call("GetForegroundWindow")
	t := call("GetWindowThreadProcessId", fg, 0)
	me := uintptr(windows.GetCurrentThreadId())
	if t != me {
		call("AttachThreadInput", me, t, 1)
		defer call("AttachThreadInput", me, t, 0)
	}
	call("BringWindowToTop", h)
	call("SetForegroundWindow", h)
	time.Sleep(250 * time.Millisecond)
}

func mouseMove(h uintptr, x, y int) {
	p := struct{ X, Y int32 }{int32(x), int32(y)}
	call("ClientToScreen", h, uintptr(unsafe.Pointer(&p)))
	// Absolute mouse motion through the Windows input queue also delivers
	// WM_POINTERUPDATE to applications using mouse-in-pointer.
	width, height := int(call("GetSystemMetrics", 0)), int(call("GetSystemMetrics", 1))
	dx := (int(p.X)*65535 + (width-1)/2) / (width - 1)
	dy := (int(p.Y)*65535 + (height-1)/2) / (height - 1)
	call("mouse_event", 0x8001, uintptr(dx), uintptr(dy), 0, 0)
	time.Sleep(80 * time.Millisecond)
}

func mouseClick(h uintptr, x, y int) {
	mouseMove(h, x, y)
	call("mouse_event", 2, 0, 0, 0, 0)
	call("mouse_event", 4, 0, 0, 0, 0)
	time.Sleep(300 * time.Millisecond)
}

// Locate the board divider on two horizontal scan lines of the actual window.
func boardDivider(h uintptr, width, height int) int {
	dc := call("GetDC", h)
	defer call("ReleaseDC", h, dc)
	pixel := windows.NewLazySystemDLL("gdi32.dll").NewProc("GetPixel")
	var first, last int
	for x := width / 2; x < width-16; x++ {
		color, _, _ := pixel.Call(dc, uintptr(x), uintptr(height/3))
		if color != 0xecece6 {
			continue
		}
		color, _, _ = pixel.Call(dc, uintptr(x), uintptr(height*2/3))
		if color == 0xecece6 {
			if first == 0 {
				first = x
			}
			last = x
		}
	}
	if first == 0 {
		return -1
	}
	return (first + last) / 2
}

func dragBoard(h uintptr) error {
	var rect struct{ Left, Top, Right, Bottom int32 }
	call("GetClientRect", h, uintptr(unsafe.Pointer(&rect)))
	width, height := int(rect.Right), int(rect.Bottom)
	fmt.Printf("Window client %dx%d, desktop %dx%d, DPI %d, focused %t\n", width, height, call("GetSystemMetrics", 0), call("GetSystemMetrics", 1), call("GetDpiForWindow", h), call("GetForegroundWindow") == h)
	start := boardDivider(h, width, height)
	if start < 0 {
		return fmt.Errorf("board divider missing in %dx%d client", width, height)
	}
	mouseMove(h, start, height/2)
	call("mouse_event", 2, 0, 0, 0, 0)
	time.Sleep(100 * time.Millisecond)
	defer call("mouse_event", 4, 0, 0, 0, 0)
	for _, offset := range []int{12, 24, 36} {
		target := start - offset
		mouseMove(h, target, height/2)
		time.Sleep(250 * time.Millisecond)
		actual := boardDivider(h, width, height)
		if actual < target-3 || actual > target+3 {
			return fmt.Errorf("board drag from %d to %d: divider at %d", start, target, actual)
		}
	}
	return nil
}

type winInput struct {
	Kind        uint32
	Pad         uint32
	VK, Scan    uint16
	Flags, Time uint32
	Pad2        uint32
	Extra       uintptr
	Tail        [8]byte
}

func input(v, scan uint16, flags uint32) {
	i := winInput{Kind: 1, VK: v, Scan: scan, Flags: flags}
	if call("SendInput", 1, uintptr(unsafe.Pointer(&i)), unsafe.Sizeof(i)) == 0 {
		panic("Windows declined keyboard input")
	}
}
func key(v uint16) { input(v, 0, 0); input(v, 0, 2) }
func ctrl(s byte) {
	input(0x11, 0, 0)
	key(uint16(s))
	input(0x11, 0, 2)
	time.Sleep(380 * time.Millisecond)
}
func typeText(s string) {
	for _, r := range utf16.Encode([]rune(s)) {
		input(0, r, 4)
		input(0, r, 6)
	}
	time.Sleep(210 * time.Millisecond)
}
func query(db *sql.DB, q string, args ...any) bool {
	var n int
	return db.QueryRow(q, args...).Scan(&n) == nil && n > 0
}
func run() error {
	app := flag.String("app", "", "release executable")
	out := flag.String("out", "", "isolated profile and evidence")
	player := flag.String("player", "", "VLC executable")
	video := flag.String("video", "", "local recording to play")
	flag.Parse()
	call("SetProcessDPIAware")
	if *out == "" || *app == "" {
		return errors.New("需要 -out 和 -app")
	}
	if err := os.MkdirAll(*out, 0700); err != nil {
		return err
	}
	okAudio := make(chan bool, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseMultipartForm(12 << 20); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		defer r.MultipartForm.RemoveAll()
		f, _, err := r.FormFile("file")
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		defer f.Close()
		b, _ := io.ReadAll(f)
		path := filepath.Join(*out, fmt.Sprintf("audio-%d.wav", time.Now().UnixNano()))
		if err = os.WriteFile(path, b, 0600); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"text":"Player audio accepted","segments":[{"text":"Player audio accepted","start":0,"end":2,"speaker":"A"}]}`)
		select {
		case okAudio <- true:
		default:
		}
	}))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if *video != "" {
		v := exec.CommandContext(ctx, *player, "--ignore-config", "--intf=dummy", "--no-plugins-cache", "--verbose=2", "--file-logging", "--logfile="+filepath.Join(*out, "vlc.log"), "--no-one-instance", "--no-qt-privacy-ask", "--no-qt-updates-notif", "--no-video-title-show", "--no-embedded-video", "--vout=wingdi", "--aout=waveout", "--start-time=300", *video)
		if err := v.Start(); err != nil {
			return err
		}
		defer func() { v.Process.Kill(); v.Wait() }()
		time.Sleep(6 * time.Second)
		fmt.Println("External VLC started", v.Process.Pid)
	}
	profile := filepath.Join(*out, "profile")
	os.Setenv("LOCALAPPDATA", profile)
	os.Setenv("SILICONFLOW_API_KEY", "local-test")
	os.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	os.Setenv("APPDATA", filepath.Join(*out, "roaming"))
	launch := func() (*exec.Cmd, uintptr, error) {
		c := exec.CommandContext(ctx, *app)
		c.Stdout = os.Stdout
		c.Stderr = os.Stderr
		if err := c.Start(); err != nil {
			return nil, 0, err
		}
		var h uintptr
		err := wait("Gio 窗口", func() bool { h = window(c.Process.Pid, "听序 ·"); return h != 0 })
		if err != nil {
			fmt.Println("Startup dialog:", title(window(c.Process.Pid, "")))
			c.Process.Kill()
			c.Wait()
			return nil, 0, err
		}
		focus(h)
		time.Sleep(1200 * time.Millisecond)
		return c, h, nil
	}
	c, h, err := launch()
	if err != nil {
		return err
	}
	defer func() {
		if c != nil && c.Process != nil {
			c.Process.Kill()
			c.Wait()
		}
	}()
	db, err := sql.Open("sqlite", filepath.Join(profile, "Tingsu", "data", "studio.sqlite3"))
	if err != nil {
		return err
	}
	defer db.Close()
	name := fmt.Sprintf("会议验收-%d", time.Now().Unix())
	if *video != "" {
		call("ShowWindow", h, 3)
		time.Sleep(time.Second)
		if err = dragBoard(h); err != nil {
			return err
		}
		dpi := int(call("GetDpiForWindow", h))
		mouseClick(h, 112*dpi/96, 18*dpi/96)
		mouseClick(h, 140*dpi/96, 55*dpi/96)
		fmt.Println("Dragged the board and clicked the File menu")
	} else {
		ctrl('N')
	}
	typeText(name)
	ctrl('S')
	if err = wait("新建会议", func() bool { return query(db, "SELECT count(*) FROM meetings WHERE title=?", name) }); err != nil {
		return err
	}
	ctrl('K')
	typeText("第五次会议跟进")
	ctrl('S')
	if err = wait("新建便签", func() bool { return query(db, "SELECT count(*) FROM notes WHERE title='第五次会议跟进'") }); err != nil {
		return err
	}
	fmt.Println("Created meeting and note through native GUI")
	if *video != "" {
		matches, _ := filepath.Glob(filepath.Join(profile, "Tingsu", "runtime", "*", "bin", "tingsu-capture.exe"))
		if len(matches) > 0 {
			for _, mode := range []string{"audio-sources", "windows"} {
				data, _ := exec.Command(matches[0], mode).CombinedOutput()
				fmt.Printf("%s %s\n", mode, data)
			}
		}
	}
	if *video != "" {
		ctrl('L')
		typeText("vlc")
		ctrl('S')
		ctrl('S')
		if err = wait("播放器音源", func() bool {
			return query(db, "SELECT count(*) FROM meetings WHERE title=? AND source LIKE 'win-app:%'", name)
		}); err != nil {
			return err
		}
		ctrl('R')
		if err = wait("ASR 收到播放器声音", func() bool { return query(db, "SELECT count(*) FROM events WHERE text='Player audio accepted'") }); err != nil {
			return err
		}
		ctrl('G')
		time.Sleep(time.Second)
		typeText("VLC")
		ctrl('S')
		frameDir := filepath.Join(profile, "Tingsu", "data", "frames", "*", "screen-*.jpg")
		if err = wait("播放器首帧", func() bool { v, _ := filepath.Glob(frameDir); return len(v) > 0 }); err != nil {
			return err
		}
		key(27)
		time.Sleep(time.Second)
		ctrl('J')
		if err = wait("播放器第二帧", func() bool { v, _ := filepath.Glob(frameDir); return len(v) > 1 }); err != nil {
			return err
		}
		key(27)
		ctrl('R')
		fmt.Println("Player process audio and two window frames captured")
	}
	srt := filepath.Join(*out, "archive.srt")
	if err = os.WriteFile(srt, []byte("1\n00:02:03,400 --> 00:02:05,900\nA：待确认的议题\n"), 0600); err != nil {
		return err
	}
	ctrl('I')
	var dlg uintptr
	if err = wait("系统逐字稿文件选择器", func() bool { dlg = window(c.Process.Pid, "导入会议逐字稿"); return dlg != 0 }); err != nil {
		return err
	}
	focus(dlg)
	typeText(srt)
	key(13)
	if err = wait("带时间戳的逐字稿", func() bool {
		return query(db, "SELECT count(*) FROM events WHERE speaker='A' AND text='待确认的议题' AND abs(at-123.4)<0.001")
	}); err != nil {
		return err
	}
	call("PostMessageW", h, 0x10, 0, 0)
	if err = c.Wait(); err != nil {
		return err
	}
	c, h, err = launch()
	if err != nil {
		return err
	}
	if !query(db, "SELECT count(*) FROM notes WHERE title='第五次会议跟进'") {
		return errors.New("便签重启后丢失")
	}
	call("PostMessageW", h, 0x10, 0, 0)
	err = c.Wait()
	c = nil
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]bool{"native_window": true, "meeting_note_reopened": true, "timestamped_import": true, "external_player": *video != ""})
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
