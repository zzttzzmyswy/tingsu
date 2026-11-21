package agent

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"sync"
	"time"
)

//go:embed guard.mjs
var guard []byte

type endpoint struct {
	server     *http.Server
	url, token string
	mu         sync.RWMutex
	handler    Tool
}

func newEndpoint() (*endpoint, error) {
	l, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return nil, e
	}
	var token [32]byte
	if _, e = rand.Read(token[:]); e != nil {
		l.Close()
		return nil, e
	}
	ep := &endpoint{url: "http://" + l.Addr().String() + "/mcp", token: hex.EncodeToString(token[:])}
	ep.server = &http.Server{ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 30 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp" {
			http.NotFound(w, r)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		if _, ok := r.Header["Origin"]; ok || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+ep.token)) != 1 {
			w.WriteHeader(403)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
		var p M
		if e := json.NewDecoder(r.Body).Decode(&p); e != nil {
			w.WriteHeader(400)
			return
		}
		rpl := mcp(p, ep.call, false)
		if rpl == nil {
			w.WriteHeader(202)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(rpl)
	})}
	go ep.server.Serve(l)
	return ep, nil
}

// Harness discovers MCP tools while opening a session, before the first user turn.
func (p *endpoint) call(name string, args M) (any, error) {
	p.mu.RLock()
	h := p.handler
	p.mu.RUnlock()
	if h == nil {
		return nil, errors.New("当前没有正在执行的 AI 请求")
	}
	return h(name, args)
}
func (p *endpoint) set(t Tool) { p.mu.Lock(); p.handler = t; p.mu.Unlock() }
func (p *endpoint) descriptor() M {
	return M{"type": "http", "name": "tingsu", "url": p.url, "headers": []M{{"name": "Authorization", "value": "Bearer " + p.token}}}
}

type deepseek struct {
	cfg         Config
	w           *wire
	tmp         string
	endpoint    *endpoint
	ids         map[string]string
	active      string
	image       bool
	options     M
	delta       func(string)
	permissions map[string]string
}

func (b *deepseek) connect(ctx context.Context) error {
	tmp, e := os.MkdirTemp("", "tingsu-dsh-")
	if e != nil {
		return e
	}
	b.tmp = tmp
	ok := false
	defer func() {
		if !ok {
			b.close()
		}
	}()
	p := filepath.Join(tmp, "meeting-tools.mjs")
	if e = os.WriteFile(p, guard, 0600); e != nil {
		return e
	}
	up := filepath.ToSlash(p)
	if runtime.GOOS == "windows" {
		up = "/" + up
	}
	u := url.URL{Scheme: "file", Path: up}
	patch := []M{{"insert": []M{{"id": "tingsu-tools", "name": u.String()}, {"id": "tingsu-office-kit", "name": "@deepseek-ai/dsh-office-kit", "config": M{"outputDirectory": filepath.Join(tmp, "office-output")}}, {"id": "tingsu-tool-office", "name": "@deepseek-ai/dsh-tool-office"}, {"id": "tingsu-skill-office", "name": "@deepseek-ai/dsh-skill-office", "config": M{"pythonEnvironment": "user"}}}}, {"id": "acp", "inject": []string{"acpAppStartup", "tingsuMeetingTools"}}, {"id": "system-prompt", "config": M{"personaPrefix": kit.Local}}, {"id": "agent-instructions", "disabled": true}, {"id": "skill-filesystem", "disabled": true}, {"id": "mcp-resources", "disabled": true}, {"id": "session-log-deepseek", "config": M{"enabled": false}}}
	patchPath := filepath.Join(tmp, "meeting.patch.yml")
	if e = os.WriteFile(patchPath, []byte(encode(patch)), 0600); e != nil {
		return e
	}
	b.w, e = startWire(b.cfg, true, "acp", "--patch", patchPath)
	if e != nil {
		return e
	}
	b.w.on = b.event
	init, stop := context.WithTimeout(ctx, 120*time.Second)
	defer stop()
	r, e := b.w.rpc(init, "initialize", M{"protocolVersion": 1, "clientCapabilities": M{}, "clientInfo": M{"name": "tingsu", "version": "2"}})
	if e != nil {
		return e
	}
	caps := obj(r["agentCapabilities"])
	sess := obj(caps["sessionCapabilities"])
	if integer(r["protocolVersion"]) != 1 || obj(caps["mcpCapabilities"])["http"] != true || sess["list"] == nil || sess["resume"] == nil || sess["close"] == nil {
		return errors.New("DeepSeek Harness 缺少所需的 ACP 会话或 HTTP MCP 能力，请更新本机 dsh")
	}
	b.image = obj(caps["promptCapabilities"])["image"] == true
	ok = true
	return nil
}
func (b *deepseek) event(p M) error {
	a := obj(p["params"])
	match := str(a["sessionId"]) == b.active && b.delta != nil
	if p["id"] == nil {
		if p["method"] == "session/update" && match {
			v := obj(a["update"])
			switch v["sessionUpdate"] {
			case "tool_call":
				b.permissions[str(v["toolCallId"])] = str(v["title"])
			case "agent_message_chunk":
				if part := obj(v["content"]); part["type"] == "text" {
					b.delta(str(part["text"]))
				}
			}
		}
		return nil
	}
	if p["method"] != "session/request_permission" {
		return b.w.write(M{"id": p["id"], "error": M{"code": -32601, "message": "Method not supported"}})
	}
	name := b.permissions[str(obj(a["toolCall"])["toolCallId"])]
	allowed := slices.Contains([]string{"read", "read_image", "glob", "grep", "skill", "office_render", "office_convert", "office_recalculate", "mcp__tingsu__load_tools"}, name)
	for _, s := range specs(true, false) {
		allowed = allowed || name == "mcp__tingsu__"+str(s["name"])
	}
	kind := "reject_once"
	if match && allowed {
		kind = "allow_once"
	}
	res := M{"outcome": "cancelled"}
	for _, s := range rows(a["options"]) {
		if s["kind"] == kind {
			res = M{"outcome": "selected", "optionId": s["optionId"]}
			break
		}
	}
	return b.w.write(M{"id": p["id"], "result": M{"outcome": res}})
}
func (b *deepseek) sessions(ctx context.Context) ([]M, error) {
	all := []M{}
	b.ids = map[string]string{}
	args := M{}
	seen := map[string]bool{}
	for {
		r, e := b.w.call(ctx, "session/list", args)
		if e != nil {
			return nil, e
		}
		for _, v := range rows(r["sessions"]) {
			id := str(v["sessionId"])
			cwd := str(v["cwd"])
			b.ids[id] = cwd
			all = append(all, M{"id": id, "title": first(str(v["title"]), filepath.Base(cwd)+" · "+cut(id, 12))})
		}
		cur := str(r["nextCursor"])
		if cur == "" {
			return all, nil
		}
		if seen[cur] {
			return nil, errors.New("Harness 返回重复分页")
		}
		seen[cur] = true
		args["cursor"] = cur
	}
}
func (b *deepseek) end() {
	if b.active != "" {
		ctx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		_, _ = b.w.rpc(ctx, "session/close", M{"sessionId": b.active})
		stop()
		b.active = ""
	}
	if b.endpoint != nil {
		b.endpoint.set(nil)
		b.endpoint.server.Close()
		b.endpoint = nil
	}
}
func (b *deepseek) open(ctx context.Context, id, title string) (string, error) {
	if id != "" && b.active == id {
		return id, nil
	}
	b.end()
	cwd := b.cfg.Workspace
	if id != "" {
		if b.ids[id] == "" {
			if _, e := b.sessions(ctx); e != nil {
				return "", e
			}
		}
		cwd = b.ids[id]
		if cwd == "" {
			return "", errors.New("找不到可续接的 Harness 会话")
		}
	}
	ep, e := newEndpoint()
	if e != nil {
		return "", e
	}
	b.endpoint = ep
	ok := false
	defer func() {
		if !ok {
			b.end()
		}
	}()
	a := M{"cwd": cwd, "mcpServers": []M{ep.descriptor()}}
	method := "session/new"
	if id != "" {
		method = "session/resume"
		a["sessionId"] = id
	}
	r, e := b.w.call(ctx, method, a)
	if e != nil {
		return "", e
	}
	id = first(id, str(r["sessionId"]))
	if id == "" {
		return "", errors.New("Harness 没有返回会话 ID")
	}
	b.active = id
	if e = b.configure(ctx, rows(r["configOptions"])); e != nil {
		return "", e
	}
	ok = true
	return id, nil
}
func acpOptions(raw []M) (M, map[string]M) {
	out := M{}
	keys := map[string]M{}
	groups := map[string][]string{"model": {"model"}, "effort": {"thought_level", "reasoning_effort"}, "speed": {"speed", "service_tier", "serviceTier"}}
	for _, f := range raw {
		for k, ids := range groups {
			if !slices.Contains(ids, str(f["id"])) && !slices.Contains(ids, str(f["category"])) {
				continue
			}
			keys[k] = f
			vals := []M{}
			for _, v := range rows(f["options"]) {
				nested := rows(v["options"])
				if len(nested) == 0 {
					nested = []M{v}
				}
				for _, item := range nested {
					id := str(item["value"])
					vals = append(vals, M{"id": id, "label": first(str(item["name"]), id)})
				}
			}
			out[k] = vals
		}
	}
	return out, keys
}
func (b *deepseek) configure(ctx context.Context, raw []M) error {
	for _, v := range [][2]string{{"model", b.cfg.Model}, {"effort", b.cfg.Effort}, {"speed", b.cfg.Speed}} {
		if v[1] == "" {
			continue
		}
		opts, fields := acpOptions(raw)
		vals, _ := opts[v[0]].([]M)
		matches := []string{}
		for _, c := range vals {
			id := str(c["id"])
			var names []string
			_ = json.Unmarshal([]byte(id), &names)
			if id == v[1] || c["label"] == v[1] || (v[0] == "model" && len(names) == 2 && names[1] == v[1]) {
				matches = append(matches, id)
			}
		}
		if len(matches) != 1 {
			return fmt.Errorf("Harness 没有唯一匹配的 %s 配置", v[0])
		}
		f := fields[v[0]]
		if matches[0] != f["currentValue"] {
			r, e := b.w.call(ctx, "session/set_config_option", M{"sessionId": b.active, "configId": f["id"], "value": matches[0]})
			if e != nil {
				return e
			}
			if opts := rows(r["configOptions"]); len(opts) > 0 {
				raw = opts
			}
		}
	}
	b.options, _ = acpOptions(raw)
	return nil
}
func (b *deepseek) send(ctx context.Context, id string, in []M, delta func(string), t Tool) (string, error) {
	if id != b.active || b.endpoint == nil {
		return "", errors.New("请重新打开 Harness 会话")
	}
	prompt := []M{}
	for _, v := range in {
		if v["type"] == "image" {
			if !b.image {
				return "", errors.New("此 Harness 路由未开放图片输入")
			}
			m, d, _ := strings.Cut(strings.TrimPrefix(str(v["url"]), "data:"), ";base64,")
			prompt = append(prompt, M{"type": "image", "mimeType": m, "data": d})
		} else {
			prompt = append(prompt, v)
		}
	}
	var answer strings.Builder
	b.permissions = map[string]string{}
	b.delta = func(s string) { answer.WriteString(s); delta(s) }
	b.endpoint.set(t)
	defer func() { b.delta = nil; b.endpoint.set(nil); b.permissions = nil }()
	ctx, stop := context.WithTimeout(ctx, 5*time.Minute)
	defer stop()
	r, e := b.w.rpc(ctx, "session/prompt", M{"sessionId": id, "prompt": prompt})
	if e != nil {
		if ctx.Err() != nil {
			b.endpoint.set(nil)
			b.delta = nil
			_ = b.w.write(M{"method": "session/cancel", "params": M{"sessionId": id}})
			cleanup, done := context.WithTimeout(context.Background(), 6*time.Second)
			defer done()
			for {
				p, err := b.w.next(cleanup)
				if err != nil {
					b.w.close()
					break
				}
				if p["method"] == nil && integer(p["id"]) == b.w.seq {
					break
				}
				_ = b.event(p)
			}
		}
		return "", e
	}
	if r["stopReason"] != "end_turn" || answer.Len() == 0 {
		return "", errors.New("DeepSeek Harness 未完成文字回复，请检查本机模型和身份配置")
	}
	return answer.String(), nil
}
func (b *deepseek) close() {
	if b.w != nil {
		b.end()
		b.w.close()
		b.w = nil
	}
	if b.tmp != "" {
		os.RemoveAll(b.tmp)
		b.tmp = ""
	}
}
