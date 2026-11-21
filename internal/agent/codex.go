package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"runtime"
	"strings"
	"time"
	"unicode/utf8"
)

type codex struct {
	cfg    Config
	w      *wire
	config M
	scoped bool
}

func (b *codex) connect(ctx context.Context) error {
	args := []string{"app-server", "-c", `web_search="disabled"`}
	for _, f := range []string{"apps", "plugins", "computer_use", "hooks", "image_generation"} {
		args = append(args, "-c", "features."+f+"=false")
	}
	for _, f := range []string{"shell_tool", "unified_exec", "view_image"} {
		args = append(args, "-c", "features."+f+"=true")
	}
	args = append(args, "-c", `shell_environment_policy.inherit="core"`)
	w, e := startWire(b.cfg, false, args...)
	if e != nil {
		return e
	}
	b.w = w
	ok := false
	defer func() {
		if !ok {
			b.close()
		}
	}()
	if _, e = w.call(ctx, "initialize", M{"clientInfo": M{"name": "tingsu", "version": "2.0.0"}, "capabilities": M{"experimentalApi": true}}); e != nil {
		return e
	}
	if e = w.write(M{"method": "initialized"}); e != nil {
		return e
	}
	a, e := w.call(ctx, "account/read", M{})
	if e != nil {
		return e
	}
	if a["account"] == nil && a["requiresOpenaiAuth"] != false {
		return errors.New("Codex 尚未登录，请先完成账号授权")
	}
	v, e := w.call(ctx, "config/read", M{"cwd": b.cfg.Workspace})
	if e != nil {
		return e
	}
	config := obj(v["config"])
	servers := M{}
	for n := range obj(config["mcp_servers"]) {
		servers[n] = M{"enabled": false}
	}
	b.config = M{"mcp_servers": servers}
	win := obj(config["windows"])
	b.scoped = runtime.GOOS != "windows" || win["sandbox"] == "elevated" || win["sandbox"] == "mxc"
	if b.scoped {
		b.config["default_permissions"] = "tingsu_files"
		b.config["permissions"] = M{"tingsu_files": M{"filesystem": M{":minimal": "read", ":workspace_roots": "read"}, "network": M{"enabled": false}}}
	} else if win["sandbox"] == nil {
		b.config["windows"] = M{"sandbox": "unelevated"}
	}
	ok = true
	return nil
}
func (b *codex) pages(ctx context.Context, method string, args M) ([]M, error) {
	out := []M{}
	seen := map[string]bool{}
	for {
		r, e := b.w.call(ctx, method, args)
		if e != nil {
			return nil, e
		}
		out = append(out, rows(r["data"])...)
		next := str(r["nextCursor"])
		if next == "" {
			return out, nil
		}
		if seen[next] {
			return nil, errors.New("服务返回重复分页")
		}
		seen[next] = true
		args["cursor"] = next
	}
}
func (b *codex) sessions(ctx context.Context) ([]M, error) {
	rows, e := b.pages(ctx, "thread/list", M{"limit": 100, "sortKey": "updated_at", "modelProviders": []string{}})
	for i, m := range rows {
		rows[i] = M{"id": m["id"], "title": cut(first(str(m["name"]), str(m["preview"]), str(m["id"])), 110)}
	}
	return rows, e
}
func (b *codex) catalog(ctx context.Context) ([]M, error) {
	raw, e := b.pages(ctx, "model/list", M{"limit": 100})
	out := []M{}
	for _, m := range raw {
		model := first(str(m["model"]), str(m["id"]))
		if model == "" || m["hidden"] == true {
			continue
		}
		effort, speed := []string{}, []string{}
		for _, v := range rows(m["supportedReasoningEfforts"]) {
			if x := str(v["reasoningEffort"]); x != "" {
				effort = append(effort, x)
			}
		}
		for _, v := range rows(m["serviceTiers"]) {
			if x := str(v["id"]); x != "" {
				speed = append(speed, x)
			}
		}
		if len(speed) == 0 {
			for _, v := range list(m["additionalSpeedTiers"]) {
				s := str(v)
				if s == "fast" {
					s = "priority"
				}
				speed = append(speed, s)
			}
		}
		out = append(out, M{"id": model, "label": first(str(m["displayName"]), model), "effort": effort, "speed": speed, "default": m["isDefault"] == true})
	}
	return out, e
}
func (b *codex) env() []M {
	return []M{{"environmentId": "local", "cwd": b.cfg.Workspace, "runtimeWorkspaceRoots": []string{b.cfg.Workspace}}}
}
func (b *codex) open(ctx context.Context, id, title string) (string, error) {
	instructions := kit.Codex
	if runtime.GOOS == "windows" {
		instructions += " Windows 使用文件清单 absolute_path 并明确将 workdir 设置为 workspace。含非 ASCII 字符的路径可用 PowerShell $p=-join [char[]](CODES) 还原 absolute_path_utf16 后使用 -LiteralPath $p。"
	}
	m := M{"cwd": b.cfg.Workspace, "approvalPolicy": "never", "runtimeWorkspaceRoots": []string{b.cfg.Workspace}, "baseInstructions": instructions, "config": b.config}
	if b.scoped {
		m["permissions"] = "tingsu_files"
	} else {
		m["sandbox"] = "read-only"
	}
	if b.cfg.Model != "" {
		m["model"] = b.cfg.Model
	}
	method := "thread/start"
	if id != "" {
		method = "thread/resume"
		m["threadId"] = id
		m["excludeTurns"] = true
	} else {
		m["environments"] = b.env()
		m["selectedCapabilityRoots"] = []string{}
	}
	r, e := b.w.call(ctx, method, m)
	if e != nil {
		return "", e
	}
	sid := str(obj(r["thread"])["id"])
	if id == "" && sid != "" && title != "" {
		_, _ = b.w.call(ctx, "thread/name/set", M{"threadId": sid, "name": cut(title, 120)})
	}
	return sid, nil
}

var prefixRE = regexp.MustCompile(`^\s*\{\s*"message"\s*:\s*"`)

func prefix(raw string) string {
	i := prefixRE.FindStringIndex(raw)
	if i == nil {
		return ""
	}
	start := i[1]
	j := start
	for j < len(raw) {
		if raw[j] == '"' {
			break
		}
		if raw[j] == '\\' {
			if j+1 >= len(raw) {
				break
			}
			if raw[j+1] == 'u' {
				if j+6 > len(raw) {
					break
				}
				j += 6
			} else {
				j += 2
			}
		} else {
			_, n := utf8.DecodeRuneInString(raw[j:])
			j += n
		}
	}
	var text string
	if json.Unmarshal([]byte("\""+raw[start:j]+"\""), &text) != nil {
		return ""
	}
	return strings.TrimSuffix(text, "\uFFFD")
}
func (b *codex) turn(ctx context.Context, id string, in []M, delta func(string)) (M, error) {
	params := M{"threadId": id, "input": in, "outputSchema": kit.Schema, "environments": b.env(), "runtimeWorkspaceRoots": []string{b.cfg.Workspace}, "approvalPolicy": "never", "cwd": b.cfg.Workspace}
	if b.scoped {
		params["permissions"] = "tingsu_files"
	} else {
		params["sandboxPolicy"] = M{"type": "readOnly", "networkAccess": false}
	}
	for k, v := range map[string]string{"model": b.cfg.Model, "effort": b.cfg.Effort, "serviceTier": b.cfg.Speed} {
		if v != "" {
			params[k] = v
		}
	}
	pending := []M{}
	b.w.on = func(p M) error {
		if p["id"] != nil {
			return b.w.write(M{"id": p["id"], "error": M{"code": -32601, "message": "Meeting tools only"}})
		}
		switch p["method"] {
		case "item/agentMessage/delta", "item/completed", "turn/completed":
			pending = append(pending, p)
		}
		return nil
	}
	defer func() { b.w.on = nil }()
	// Collect the turn ID even when the user presses stop during the start handshake.
	startCtx, stop := context.WithTimeout(context.WithoutCancel(ctx), 20*time.Second)
	r, e := b.w.rpc(startCtx, "turn/start", params)
	stop()
	if e != nil {
		return nil, e
	}
	tid := str(obj(r["turn"])["id"])
	if tid == "" {
		return nil, errors.New("Codex 未返回轮次 ID")
	}
	finished := false
	defer func() {
		if !finished {
			ctx, stop := context.WithTimeout(context.Background(), 3*time.Second)
			defer stop()
			_, _ = b.w.rpc(ctx, "turn/interrupt", M{"threadId": id, "turnId": tid})
		}
	}()
	buffers := map[string]string{}
	emitted := map[string]int{}
	final := ""
	emit := func(key, raw string) {
		visible := prefix(raw)
		if len(visible) > emitted[key] {
			delta(visible[emitted[key]:])
			emitted[key] = len(visible)
		}
	}
	for {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		if len(pending) == 0 {
			timeout, stop := context.WithTimeout(ctx, 180*time.Second)
			p, e := b.w.next(timeout)
			stop()
			if e != nil {
				return nil, e
			}
			if e = b.w.dispatch(p); e != nil {
				return nil, e
			}
			continue
		}
		p := pending[0]
		pending = pending[1:]
		a := obj(p["params"])
		if a["threadId"] != id {
			continue
		}
		if p["method"] == "turn/completed" {
			t := obj(a["turn"])
			if t["id"] != tid {
				continue
			}
			finished = true
			if t["status"] != "completed" {
				return nil, fmt.Errorf("Codex：%s", first(str(obj(t["error"])["message"]), str(t["status"])))
			}
			break
		}
		if a["turnId"] != tid {
			continue
		}
		if p["method"] == "item/agentMessage/delta" {
			k := str(a["itemId"])
			buffers[k] += str(a["delta"])
			emit(k, buffers[k])
		} else if i := obj(a["item"]); i["type"] == "agentMessage" && i["phase"] != "commentary" {
			final = str(i["text"])
			emit(str(i["id"]), final)
		}
	}
	var result M
	if json.Unmarshal([]byte(final), &result) != nil {
		return nil, errors.New("Codex 回复格式不完整")
	}
	return result, nil
}
func (b *codex) send(ctx context.Context, id string, in []M, delta func(string), t Tool) (string, error) {
	if !b.scoped {
		if e := refreshReadACL(b.cfg.Workspace); e != nil {
			return "", e
		}
	}
	parts := []string{}
	for range 8 {
		started := false
		r, e := b.turn(ctx, id, in, func(s string) {
			if !started && len(parts) > 0 {
				delta("\n")
			}
			started = true
			delta(s)
		})
		if e != nil {
			return "", e
		}
		if s := str(r["message"]); s != "" {
			parts = append(parts, s)
		}
		actions := rows(r["actions"])
		if len(actions) == 0 {
			return strings.Join(parts, "\n"), nil
		}
		outs := []M{}
		for _, a := range actions {
			outs = append(outs, M{"name": a["name"], "output": invoke(t, str(a["name"]), a["arguments"], true, false)})
		}
		in = []M{{"type": "text", "text": encode(M{"meeting_tool_results": outs})}}
	}
	return "", errors.New("已完成八轮会议工具调用")
}
func (b *codex) close() { b.w.close(); b.w = nil }
