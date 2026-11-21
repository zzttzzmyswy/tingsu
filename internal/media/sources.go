// Package media discovers native desktop audio streams and decodes audio/video.
package media

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"time"
)

type Source struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	Kind  string `json:"kind"`
}

func output(ctx context.Context, name string, args ...string) []byte {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	b, _ := command(ctx, name, args...).Output()
	return b
}
func text(m map[string]any, key string) string {
	v := m[key]
	if v == nil {
		return ""
	}
	switch n := v.(type) {
	case float64:
		return strconv.FormatFloat(n, 'f', -1, 64)
	case string:
		return n
	case json.Number:
		return n.String()
	default:
		return fmt.Sprint(v)
	}
}
func first(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v := text(m, k); v != "" {
			return v
		}
	}
	return ""
}
func obj(m map[string]any, k string) map[string]any { v, _ := m[k].(map[string]any); return v }
func avail(name string) bool                        { _, e := exec.LookPath(name); return e == nil }
func records(raw []byte) (out []map[string]any) {
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.UseNumber()
	if d.Decode(&out) != nil {
		return nil
	}
	return out
}
func pulse(ctx context.Context, group string) []map[string]any {
	rows := records(output(ctx, "pactl", "-f", "json", "list", group))
	if rows != nil {
		return rows
	}
	for _, line := range strings.Split(string(output(ctx, "pactl", "list", "short", group)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) > 1 {
			k := "name"
			if group == "sink-inputs" {
				k = "sink"
			}
			rows = append(rows, map[string]any{"index": fields[0], k: fields[1]})
		}
	}
	return rows
}
func pipeSources(ctx context.Context) []Source {
	if !avail("pw-record") {
		return nil
	}
	out := []Source{}
	for _, v := range records(output(ctx, "pw-dump")) {
		p := obj(obj(v, "info"), "props")
		kind, label := "", ""
		switch text(p, "media.class") {
		case "Audio/Source", "Audio/Source/Virtual":
			kind, label = "mic", "麦克风"
		case "Audio/Sink":
			kind, label = "monitor", "系统输出"
		case "Stream/Output/Audio":
			kind, label = "app", "应用"
		default:
			continue
		}
		id := text(p, "object.serial")
		if !strings.HasSuffix(text(v, "type"), ":Node") || id == "" {
			continue
		}
		name := first(p, "application.name", "node.description", "node.nick", "node.name", "object.serial")
		if extra := text(p, "media.name"); kind == "app" && extra != "" && name != extra {
			name += " · " + extra
		}
		out = append(out, Source{id, label + " · " + name + " (PipeWire)", "pipewire-" + kind})
	}
	return out
}
func pulseSources(ctx context.Context) []Source {
	if !avail("pactl") || !avail("parec") {
		return nil
	}
	out := []Source{}
	for _, r := range pulse(ctx, "sources") {
		n := text(r, "name")
		if n == "" {
			continue
		}
		kind, label := "mic", "麦克风"
		if strings.HasSuffix(n, ".monitor") {
			kind, label = "monitor", "系统输出"
		}
		out = append(out, Source{n, label + " · " + first(r, "description", "name") + " (PulseAudio)", "pulse-" + kind})
	}
	for _, r := range pulse(ctx, "sink-inputs") {
		props := obj(r, "properties")
		id := text(r, "index")
		name := first(props, "application.name", "media.name")
		if name == "" {
			name = id
		}
		if extra := text(props, "media.name"); extra != "" && extra != name {
			name += " · " + extra
		}
		out = append(out, Source{id, "应用 · " + name + " (PulseAudio)", "pulse-app"})
	}
	return out
}

var alsaDevice = regexp.MustCompile(`(?m)^(\d+)-(\d+):\s*([^:]+):.*\bcapture\s+[1-9]`)

func alsaSources() []Source {
	if !avail("arecord") {
		return nil
	}
	b, _ := os.ReadFile("/proc/asound/pcm")
	out := []Source{}
	for _, m := range alsaDevice.FindAllStringSubmatch(string(b), -1) {
		card, _ := strconv.Atoi(m[1])
		dev, _ := strconv.Atoi(m[2])
		addr := fmt.Sprintf("plughw:%d,%d", card, dev)
		out = append(out, Source{addr, "麦克风 · " + strings.TrimSpace(m[3]) + " (ALSA " + addr + ")", "alsa-mic"})
	}
	return out
}
func Sources(ctx context.Context) ([]Source, error) {
	if runtime.GOOS == "windows" {
		timeout, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		b, err := command(timeout, "tingsu-capture.exe", "audio-sources").Output()
		if err != nil {
			return nil, fmt.Errorf("无法访问 Windows 音频设备: %w", err)
		}
		out := []Source{}
		for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
			if strings.TrimSpace(line) == "" {
				continue
			}
			var s Source
			if err := json.Unmarshal([]byte(line), &s); err != nil {
				return nil, err
			}
			label := map[string]string{"win-mic": "麦克风", "win-monitor": "系统输出", "win-app": "应用"}[s.Kind]
			s.Label = label + " · " + s.Label + " (WASAPI)"
			out = append(out, s)
		}
		return out, nil
	}
	out := append([]Source{}, pipeSources(ctx)...)
	out = append(out, pulseSources(ctx)...)
	out = append(out, alsaSources()...)
	rank := func(s Source) int {
		if strings.HasSuffix(s.Kind, "-mic") {
			return 0
		}
		if strings.HasSuffix(s.Kind, "-app") {
			return 1
		}
		return 2
	}
	slices.SortStableFunc(out, func(a, b Source) int { return rank(a) - rank(b) })
	return out, ctx.Err()
}
func audioCommand(ctx context.Context, s Source) (*exec.Cmd, error) {
	id := s.ID
	switch s.Kind {
	case "win-mic", "win-app", "win-monitor":
		return command(ctx, "tingsu-capture.exe", "audio", s.Kind, id), nil
	case "pipewire-mic", "pipewire-monitor", "pipewire-app":
		p := map[string]any{"node.dont-fallback": true, "node.dont-reconnect": true, "application.name": "Meeting Agent"}
		if s.Kind == "pipewire-monitor" {
			p["stream.capture.sink"] = true
		}
		v, _ := json.Marshal(p)
		return command(ctx, "pw-record", "--target", id, "-P", string(v), "--raw", "--format", "s16", "--rate", "16000", "--channels", "1", "-"), nil
	case "pulse-mic", "pulse-monitor", "pulse-app":
		a := []string{"--raw", "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=100", "--client-name=Meeting Agent"}
		if s.Kind == "pulse-app" {
			sink := ""
			for _, v := range pulse(ctx, "sink-inputs") {
				if text(v, "index") == id {
					sink = text(v, "sink")
					break
				}
			}
			var monitor string
			for _, v := range pulse(ctx, "sinks") {
				if sink != "" && text(v, "index") == sink {
					monitor = text(v, "monitor_source")
					if monitor == "" {
						monitor = text(v, "name") + ".monitor"
					}
					break
				}
			}
			if monitor == "" {
				return nil, errors.New("此应用的播放流已关闭，请刷新音源列表")
			}
			a = append(a, "--monitor-stream", id)
			id = monitor
		}
		return command(ctx, "parec", append(a, "--device", id)...), nil
	case "alsa-mic":
		return command(ctx, "arecord", "-q", "-D", id, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"), nil
	default:
		return nil, fmt.Errorf("未知音源类型：%s", s.Kind)
	}
}
