// This executable drives the single-file backend over its normal desktop pipe on a
// private Windows audio test machine. The native WebView2 UI is exercised separately.
package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type state struct {
	Sources []struct{ ID string }
	Meeting struct{ Title string }
	Events  []struct{ Kind, Text string }
	Capture string
}

func host() error {
	out := json.NewEncoder(os.Stdout)
	cmd := func(name string, args map[string]any) error {
		return out.Encode(map[string]any{"type": "command", "name": name, "args": args})
	}
	if err := out.Encode(map[string]string{"type": "ready"}); err != nil {
		return err
	}
	step := 0
	scan := bufio.NewScanner(os.Stdin)
	scan.Buffer(make([]byte, 4096), 8<<20)
	for scan.Scan() {
		var p struct {
			Type, Name string
			Args       []json.RawMessage
		}
		if err := json.Unmarshal(scan.Bytes(), &p); err != nil {
			return err
		}
		if p.Type == "quit" {
			return nil
		}
		if p.Type != "signal" || p.Name != "state" || len(p.Args) != 1 {
			continue
		}
		var payload string
		var s state
		if err := json.Unmarshal(p.Args[0], &payload); err != nil {
			return err
		}
		if err := json.Unmarshal([]byte(payload), &s); err != nil {
			return err
		}
		switch step {
		case 0:
			for _, src := range s.Sources {
				if strings.HasPrefix(src.ID, "win-app:"+os.Getenv("QA_TONE_PID")+"/") {
					step++
					if err := cmd("new", map[string]any{"title": "WASAPI release check", "source": src.ID, "start": true}); err != nil {
						return err
					}
					break
				}
			}
		case 1:
			for _, ev := range s.Events {
				if ev.Kind == "speech" && ev.Text == "Verified target application audio" {
					step++
					if err := cmd("stop", nil); err != nil {
						return err
					}
					break
				}
			}
		case 2:
			if s.Capture == "idle" && s.Meeting.Title == "WASAPI release check" {
				if err := os.WriteFile(os.Getenv("QA_RESULT"), []byte("ok"), 0600); err != nil {
					return err
				}
				step++
				if err := out.Encode(map[string]string{"type": "close-request"}); err != nil {
					return err
				}
			}
		}
	}
	return scan.Err()
}
func amplitude(p []byte, hz float64) float64 {
	var x, y float64
	for i := 0; i+1 < len(p); i += 2 {
		sample := float64(int16(binary.LittleEndian.Uint16(p[i:])))
		sin, cos := math.Sincos(math.Pi * hz * float64(i) / 16000)
		x += sample * cos
		y += sample * sin
	}
	return 4 * math.Hypot(x, y) / float64(len(p))
}
func run(app, player string) error {
	root, e := os.MkdirTemp("", "tingsu-audio-flow")
	if e != nil {
		return e
	}
	defer os.RemoveAll(root)
	self, e := os.Executable()
	if e != nil {
		return e
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	var pid int
	for _, hz := range []string{"749", "1439"} {
		p := exec.CommandContext(ctx, player, "--tone", hz)
		if e := p.Start(); e != nil {
			return e
		}
		defer func() { p.Process.Kill(); p.Wait() }()
		if hz == "749" {
			pid = p.Process.Pid
		}
	}
	time.Sleep(1500 * time.Millisecond)
	observed := make(chan bool, 8)
	httpd := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f, _, err := r.FormFile("file")
		if err != nil {
			http.Error(w, "missing audio", 400)
			return
		}
		defer f.Close()
		wav, err := io.ReadAll(io.LimitReader(f, 4<<20))
		if err != nil || len(wav) < 16044 || string(wav[:4]) != "RIFF" {
			http.Error(w, "invalid wave", 400)
			return
		}
		a, b := amplitude(wav[44:], 749), amplitude(wav[44:], 1439)
		ok := a > 100 && a > 12*b
		fmt.Printf("Uploaded %d samples, target %.2f, second application %.2f\n", (len(wav)-44)/2, a, b)
		select {
		case observed <- ok:
		default:
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"text":"Verified target application audio"}`)
	}))
	defer httpd.Close()
	c := exec.CommandContext(ctx, app)
	result := filepath.Join(root, "rendered")
	c.Env = append(os.Environ(), "LOCALAPPDATA="+root, "TINGSU_DESKTOP_HOST="+self, "QA_TONE_PID="+strconv.Itoa(pid), "QA_RESULT="+result, "TINGSU_SILICONFLOW_URL="+httpd.URL, "SILICONFLOW_API_KEY=test-only")
	c.Stdout = os.Stdout
	c.Stderr = os.Stderr
	if err := c.Run(); err != nil {
		return err
	}
	if _, e = os.Stat(result); e != nil {
		return errors.New("no persisted subtitle returned to desktop")
	}
	select {
	case ok := <-observed:
		if !ok {
			return errors.New("application streams were mixed")
		}
	default:
		return errors.New("no ASR upload received")
	}
	fmt.Println("PASS release EXE selected WASAPI process, ASR upload, timestamped transcript, stop and close")
	return nil
}
func main() {
	var err error
	switch {
	case len(os.Args) == 2 && strings.EqualFold(filepath.Base(os.Args[1]), "index.html"):
		err = host()
	case len(os.Args) == 3:
		err = run(os.Args[1], os.Args[2])
	default:
		err = errors.New("usage: audioflow.exe path-to-Tingsu.exe path-to-tone-test.exe")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
