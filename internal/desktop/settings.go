package desktop

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"unicode"
)

type M = map[string]any

func str(v any) string { s, _ := v.(string); return s }
func number(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int64:
		return float64(n)
	case int:
		return float64(n)
	case json.Number:
		x, _ := n.Float64()
		return x
	}
	return 0
}
func truth(v any) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	return number(v) != 0
}
func encoded(v any) string { b, _ := json.Marshal(v); return string(b) }
func stringsOf(v any) []string {
	if a, ok := v.([]string); ok {
		return a
	}
	var out []string
	if a, ok := v.([]any); ok {
		for _, item := range a {
			if s := str(item); s != "" {
				out = append(out, s)
			}
		}
	}
	return out
}
func appDir(area string) string {
	home, _ := os.UserHomeDir()
	if runtime.GOOS == "windows" {
		base := os.Getenv("LOCALAPPDATA")
		if base == "" {
			base = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(base, "Tingsu", area)
	}
	base := os.Getenv("XDG_" + strings.ToUpper(area) + "_HOME")
	if base == "" {
		base = filepath.Join(home, map[string]string{"data": ".local/share", "cache": ".cache", "config": ".config"}[area])
	}
	return filepath.Join(base, "tingsu")
}

var providers = M{
	"codex":    M{"title": "Codex", "label": "Codex · 本地 CLI", "hint": "使用本机安装的 Codex CLI 和登录身份。", "cli": "codex"},
	"openai":   M{"title": "OpenAI", "label": "OpenAI · Conversations API", "hint": "使用 OpenAI API 密钥创建或继续 API 会话。", "cli": ""},
	"claude":   M{"title": "Claude Code", "label": "Claude Code · 本地 CLI", "hint": "使用本机已授权的 Claude Code。", "cli": "claude"},
	"deepseek": M{"title": "DeepSeek Harness", "label": "DeepSeek Harness · 本地 CLI", "hint": "连接本机 dsh 的 ACP 服务。", "cli": "dsh"},
}

func defaults() M {
	return M{"provider": "siliconflow", "siliconflow_model": "XingChenAGI/XingChenASR-Diarize-V3.0", "sophnet_model": "Fun-ASR", "agent": "codex", "local_agent_options": M{}, "model": "", "codex_path": "", "claude_path": "", "deepseek_path": "", "auto": true, "share_frames": false, "chunk": 12, "selected": ""}
}

type settings struct {
	path   string
	values M
}

func readSettings() (settings, error) {
	s := settings{filepath.Join(appDir("config"), "settings.json"), defaults()}
	b, err := os.ReadFile(s.path)
	if os.IsNotExist(err) {
		return s, nil
	}
	if err == nil {
		err = json.Unmarshal(b, &s.values)
	}
	return s, err
}
func (s *settings) save(values M) error {
	next := M{}
	for k, v := range s.values {
		next[k] = v
	}
	for k, v := range values {
		next[k] = v
	}
	b, e := json.MarshalIndent(next, "", "  ")
	if e != nil {
		return e
	}
	if e = os.MkdirAll(filepath.Dir(s.path), 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(s.path), ".settings-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if _, e = f.Write(b); e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e != nil {
		return e
	}
	if ce != nil {
		return ce
	}
	if e = os.Rename(f.Name(), s.path); e == nil {
		s.values = next
	}
	return e
}
func (s settings) key(p string) string {
	if k := os.Getenv(strings.ToUpper(p) + "_API_KEY"); k != "" {
		return k
	}
	return str(s.values[p+"_key"])
}
func (s settings) public() M {
	out := defaults()
	for k := range out {
		out[k] = s.values[k]
	}
	keys := M{}
	for _, p := range []string{"siliconflow", "sophnet", "openai"} {
		keys[p] = s.key(p) != ""
	}
	out["hasKeys"] = keys
	return out
}
func prefs(raw any) (M, error) {
	m, ok := raw.(M)
	if raw != nil && !ok {
		return nil, errors.New("AI 推理选项无效")
	}
	out := M{"model": "", "effort": "", "speed": ""}
	for k, v := range m {
		s, ok := v.(string)
		if _, known := out[k]; !known || !ok || len([]rune(s)) > 512 || strings.ContainsFunc(s, unicode.IsControl) {
			return nil, fmt.Errorf("无效的 AI 推理选项 %s", k)
		}
		out[k] = strings.TrimSpace(s)
	}
	return out, nil
}
