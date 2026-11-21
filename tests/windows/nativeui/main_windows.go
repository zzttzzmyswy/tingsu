// Drive the packaged Go window over the Windows desktop, using real keyboard
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
		v := exec.CommandContext(ctx, *player, "--no-one-instance", "--no-qt-privacy-ask", "--no-qt-updates-notif", "--no-video-title-show", "--no-embedded-video", "--vout=wingdi", "--aout=mmdevice", "--start-time=300", *video)
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
	ctrl('N')
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
