package agent

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"time"
)

type claude struct {
	cfg              Config
	flags            map[string]bool
	drafts, projects string
}

func (b *claude) probe(ctx context.Context, args ...string) ([]byte, error) {
	ctx, stop := context.WithTimeout(ctx, 30*time.Second)
	defer stop()
	cmd, e := Command(ctx, b.cfg, args...)
	if e != nil {
		return nil, e
	}
	return cmd.Output()
}
func (b *claude) connect(ctx context.Context) error {
	help, e := b.probe(ctx, "--help")
	if e != nil {
		return check(e, "Claude Code 启动失败")
	}
	b.flags = map[string]bool{}
	for _, f := range regexp.MustCompile("--[a-z][a-z0-9-]*").FindAllString(string(help), -1) {
		b.flags[f] = true
	}
	for _, f := range []string{"--input-format", "--output-format", "--strict-mcp-config", "--tools"} {
		if !b.flags[f] {
			return errors.New("所选程序不支持 Claude Code 双向 stream-json 协议")
		}
	}
	if b.cfg.Effort != "" && !b.flags["--effort"] {
		return errors.New("此 CLI 没有 --effort 选项")
	}
	if b.cfg.Speed != "" && (!b.flags["--settings"] || !slices.Contains([]string{"fast", "standard"}, b.cfg.Speed)) {
		return errors.New("此 Claude CLI 不支持选定速率")
	}
	var status M
	if regexp.MustCompile(`(?m)^\s*auth(?:\s|$)`).Match(help) {
		r, _ := b.probe(ctx, "auth", "status", "--json")
		_ = json.Unmarshal(r, &status)
	}
	home, _ := os.UserHomeDir()
	cfg := first(str(status["configDirectory"]), os.Getenv("CLAUDE_CONFIG_DIR"), filepath.Join(home, ".claude"))
	b.projects = first(str(status["projectsDirectory"]), filepath.Join(cfg, "projects"))
	b.drafts = filepath.Join(b.cfg.DataDir, "claude-workspace", "drafts", digest(cfg)[:20])
	if status["loggedIn"] == false {
		settings := M{}
		data, _ := os.ReadFile(filepath.Join(cfg, "settings.json"))
		_ = json.Unmarshal(data, &settings)
		ok := settings["apiKeyHelper"] != nil
		for _, k := range []string{"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"} {
			ok = ok || first(os.Getenv(k), str(obj(settings["env"])[k])) != ""
		}
		if !ok {
			return errors.New("Claude Code 尚未登录，请在终端检查 claude auth status")
		}
	}
	return os.MkdirAll(b.drafts, 0700)
}

var uuidRE = regexp.MustCompile("(?i)^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")

func uuid() string {
	p := make([]byte, 16)
	_, _ = rand.Read(p)
	p[6] = (p[6] & 15) | 64
	p[8] = (p[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", p[:4], p[4:6], p[6:8], p[8:10], p[10:])
}
func claudeTitle(p string) string {
	f, e := os.Open(p)
	if e != nil {
		return ""
	}
	defer f.Close()
	prefix, _ := io.ReadAll(io.LimitReader(f, 8192))
	st, _ := f.Stat()
	if st.Size() > 8192 {
		_, _ = f.Seek(max(int64(8192), st.Size()-8192), 0)
		tail, _ := io.ReadAll(io.LimitReader(f, 8192))
		prefix = append(append(prefix, '\n'), tail...)
	}
	title, hint := "", ""
	for _, line := range strings.Split(string(prefix), "\n") {
		var m M
		if json.Unmarshal([]byte(line), &m) != nil {
			continue
		}
		title = first(str(m["customTitle"]), title)
		hint = first(str(m["aiTitle"]), str(m["lastPrompt"]), str(m["summary"]), hint)
		if hint == "" && m["type"] == "user" {
			v := obj(m["message"])["content"]
			hint = str(v)
			for _, b := range rows(v) {
				hint = first(hint, str(b["text"]))
			}
		}
	}
	return cut(first(title, hint), 110)
}
func (b *claude) native(id string) string {
	ps, _ := filepath.Glob(filepath.Join(b.projects, "*", id+".jsonl"))
	var newest time.Time
	result := ""
	for _, p := range ps {
		if i, e := os.Stat(p); e == nil && i.ModTime().After(newest) {
			newest = i.ModTime()
			result = p
		}
	}
	return result
}
func (b *claude) sessions(ctx context.Context) ([]M, error) {
	out := []M{}
	ps, _ := filepath.Glob(filepath.Join(b.projects, "*", "*.jsonl"))
	drafts, _ := filepath.Glob(filepath.Join(b.drafts, "*.txt"))
	times := map[string]time.Time{}
	titles := map[string]string{}
	for _, p := range append(drafts, ps...) {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		id := strings.TrimSuffix(filepath.Base(p), filepath.Ext(p))
		if !uuidRE.MatchString(id) {
			continue
		}
		i, e := os.Stat(p)
		if e != nil || i.ModTime().Before(times[id]) {
			continue
		}
		times[id] = i.ModTime()
		if strings.HasSuffix(p, ".txt") {
			b, _ := os.ReadFile(p)
			titles[id] = string(b)
		} else {
			titles[id] = claudeTitle(p)
		}
	}
	for id, t := range titles {
		out = append(out, M{"id": id, "title": first(t, id)})
	}
	sort.Slice(out, func(i, j int) bool { return times[str(out[i]["id"])].After(times[str(out[j]["id"])]) })
	return out, nil
}
func (b *claude) open(ctx context.Context, id, title string) (string, error) {
	if id == "" {
		id = uuid()
		return id, os.WriteFile(filepath.Join(b.drafts, id+".txt"), []byte(cut(first(title, "会议助手"), 110)), 0600)
	}
	if uuidRE.MatchString(id) {
		if _, e := os.Stat(filepath.Join(b.drafts, id+".txt")); e == nil || b.native(id) != "" {
			return id, nil
		}
	}
	return "", errors.New("找不到此 Claude Code 会话")
}
func (b *claude) resume(p string) (string, error) {
	if b.flags["--safe-mode"] {
		return p, nil
	}
	key := regexp.MustCompile("[^a-zA-Z0-9]").ReplaceAllString(b.cfg.Workspace, "-")
	d := filepath.Join(b.projects, key)
	if e := os.MkdirAll(d, 0700); e != nil {
		return "", e
	}
	target := filepath.Join(d, filepath.Base(p))
	if _, e := os.Stat(target); os.IsNotExist(e) {
		if e = os.Link(p, target); e != nil && !os.IsExist(e) {
			f, e := os.Open(p)
			if e != nil {
				return "", e
			}
			defer f.Close()
			w, e := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e != nil {
				return "", e
			}
			_, e = io.Copy(w, f)
			ce := w.Close()
			if e == nil {
				e = ce
			}
			if e != nil {
				os.Remove(target)
				return "", e
			}
		}
	}
	return strings.TrimSuffix(filepath.Base(p), ".jsonl"), nil
}
func (b *claude) send(ctx context.Context, id string, in []M, delta func(string), tool Tool) (string, error) {
	args := []string{"-p", "--verbose", "--strict-mcp-config", "--tools", "Read,Grep,Glob", "--permission-prompt-tool", "stdio", "--input-format", "stream-json", "--output-format", "stream-json", "--system-prompt", kit.Local, "--max-turns", "12"}
	draft := filepath.Join(b.drafts, id+".txt")
	if native := b.native(id); native != "" {
		p, e := b.resume(native)
		if e != nil {
			return "", e
		}
		args = append(args, "--resume", p)
	} else {
		title, e := os.ReadFile(draft)
		if e != nil {
			return "", errors.New("本机 Claude 会话已移除")
		}
		args = append(args, "--session-id", id)
		if b.flags["--name"] {
			args = append(args, "--name", string(title))
		}
	}
	if b.cfg.Model != "" {
		args = append(args, "--model", b.cfg.Model)
	}
	if b.cfg.Effort != "" {
		args = append(args, "--effort", b.cfg.Effort)
	}
	settings := M{"permissions": M{"ask": []string{"Read", "Grep", "Glob"}, "blockReadsOutsideWorkingDirectories": true}}
	if b.flags["--safe-mode"] {
		args = append(args, "--safe-mode")
	} else {
		settings["disableAllHooks"] = true
	}
	if b.cfg.Speed != "" {
		settings["fastMode"] = b.cfg.Speed == "fast"
	}
	if b.flags["--settings"] {
		args = append(args, "--settings", encode(settings))
	}
	if b.flags["--setting-sources"] {
		args = append(args, "--setting-sources", "user")
	}
	for _, f := range []string{"--no-chrome", "--disable-slash-commands", "--include-partial-messages"} {
		if b.flags[f] {
			args = append(args, f)
		}
	}
	if b.flags["--system-prompt-snapshot"] {
		args = append(args, "--system-prompt-snapshot", "off")
	}
	names := []string{}
	for _, s := range specs(true, true) {
		names = append(names, "mcp__meeting__"+str(s["name"]))
	}
	args = append(args, "--mcp-config", `{"mcpServers":{"meeting":{"type":"sdk","name":"meeting"}}}`, "--allowedTools", strings.Join(names, ","))
	ctx, stop := context.WithTimeout(ctx, 5*time.Minute)
	defer stop()
	cmd, e := Command(ctx, b.cfg, args...)
	if e != nil {
		return "", e
	}
	input, e := cmd.StdinPipe()
	if e != nil {
		return "", e
	}
	output, e := cmd.StdoutPipe()
	if e != nil {
		return "", e
	}
	if e = cmd.Start(); e != nil {
		return "", e
	}
	defer func() { input.Close(); killProcess(cmd); cmd.Wait() }()
	write := json.NewEncoder(input).Encode
	if e = write(M{"type": "control_request", "request_id": "init", "request": M{"subtype": "initialize", "skills": []string{}}}); e != nil {
		return "", e
	}
	var partial strings.Builder
	scan := bufio.NewScanner(output)
	scan.Buffer(make([]byte, 8192), 64<<20)
	for scan.Scan() {
		var m M
		if e = json.Unmarshal(scan.Bytes(), &m); e != nil {
			return "", e
		}
		switch m["type"] {
		case "control_response":
			r := obj(m["response"])
			if r["request_id"] != "init" {
				continue
			}
			if r["subtype"] != "success" {
				return "", errors.New("Claude Code 初始化失败")
			}
			blocks := []M{}
			for _, v := range in {
				if v["type"] == "image" {
					mt, data, _ := strings.Cut(strings.TrimPrefix(str(v["url"]), "data:"), ";base64,")
					blocks = append(blocks, M{"type": "image", "source": M{"type": "base64", "media_type": mt, "data": data}})
				} else {
					blocks = append(blocks, v)
				}
			}
			e = write(M{"type": "user", "session_id": id, "parent_tool_use_id": nil, "message": M{"role": "user", "content": blocks}})
		case "control_request":
			e = write(b.control(m, tool))
		case "stream_event":
			v := obj(obj(m["event"])["delta"])
			if v["type"] == "text_delta" {
				s := str(v["text"])
				partial.WriteString(s)
				delta(s)
			}
		case "result":
			if sid := str(m["session_id"]); sid != "" && sid != id {
				return "", errors.New("Claude 回复的会话 ID 不匹配")
			}
			if m["subtype"] != "success" || m["is_error"] == true {
				return "", errors.New("Claude Code 请求失败，请检查 CLI 的登录、模型和额度配置")
			}
			answer := first(str(m["result"]), partial.String())
			if partial.Len() == 0 {
				delta(answer)
			}
			input.Close()
			if b.native(id) != "" {
				os.Remove(draft)
			}
			return answer, nil
		}
		if e != nil {
			return "", e
		}
	}
	if e := ctx.Err(); e != nil {
		return "", e
	}
	return "", errors.New("Claude Code 流提前结束，请检查本机账号、模型及 CLI 版本")
}
func (b *claude) control(p M, t Tool) M {
	req := obj(p["request"])
	reply := M{"request_id": p["request_id"], "subtype": "success"}
	switch req["subtype"] {
	case "mcp_message":
		if req["server_name"] != "meeting" {
			reply["subtype"] = "error"
			reply["error"] = "Unknown MCP server"
			break
		}
		r := mcp(obj(req["message"]), t, true)
		if r == nil {
			r = M{"jsonrpc": "2.0", "result": M{}}
		}
		reply["response"] = M{"mcp_response": r}
	case "can_use_tool":
		args := obj(req["input"])
		name := str(req["tool_name"])
		path := first(str(args["path"]), ".")
		if name == "Read" {
			path = str(args["file_path"])
		}
		pat := str(args["pattern"])
		_, e := within(b.cfg.Workspace, path)
		valid := path != "" && slices.Contains([]string{"Read", "Glob", "Grep"}, name) && e == nil
		if name == "Glob" {
			valid = valid && pat != "" && filepath.IsLocal(pat) && !slices.Contains(strings.Split(filepath.ToSlash(pat), "/"), "..")
		}
		if valid {
			reply["response"] = M{"behavior": "allow", "updatedInput": args}
		} else {
			reply["response"] = M{"behavior": "deny", "message": "请选择本次会议工作目录中的文件。"}
		}
	default:
		reply["subtype"] = "error"
		reply["error"] = "Unsupported control request"
	}
	return M{"type": "control_response", "response": reply}
}
func (b *claude) close() {}
