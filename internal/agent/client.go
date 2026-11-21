// Package agent connects meeting turns to user-installed CLI agents.
package agent

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sync"
)

type M = map[string]any
type Config struct {
	Provider, Model, Executable, Effort, Speed, Workspace, DataDir string
}
type Tool func(string, M) (any, error)
type backend interface {
	connect(context.Context) error
	sessions(context.Context) ([]M, error)
	open(context.Context, string, string) (string, error)
	send(context.Context, string, []M, func(string), Tool) (string, error)
	close()
}
type Client struct {
	cfg     Config
	impl    backend
	op      sync.Mutex
	mu      sync.RWMutex
	session string
	life    context.Context
	stop    context.CancelFunc
}

func New(c Config) (*Client, error) {
	if c.Workspace == "" {
		c.Workspace = filepath.Join(c.DataDir, "agent-workspaces", c.Provider)
	}
	p, e := filepath.Abs(c.Workspace)
	if e != nil {
		return nil, e
	}
	c.Workspace = p
	if e = os.MkdirAll(p, 0700); e != nil {
		return nil, e
	}
	life, stop := context.WithCancel(context.Background())
	client := &Client{cfg: c, life: life, stop: stop}
	switch c.Provider {
	case "codex":
		client.impl = &codex{cfg: c}
	case "claude":
		client.impl = &claude{cfg: c}
	case "deepseek":
		client.impl = &deepseek{cfg: c}
	default:
		stop()
		return nil, errors.New("未知的 AI 服务")
	}
	return client, nil
}
func (c *Client) scope(ctx context.Context) (context.Context, func()) {
	ctx, cancel := context.WithCancel(ctx)
	remove := context.AfterFunc(c.life, cancel)
	if c.life.Err() != nil {
		cancel()
	}
	return ctx, func() { remove(); cancel() }
}
func (c *Client) Workspace() string { return c.cfg.Workspace }
func (c *Client) SessionID() string { c.mu.RLock(); defer c.mu.RUnlock(); return c.session }
func (c *Client) Connect(ctx context.Context) error {
	c.op.Lock()
	defer c.op.Unlock()
	ctx, done := c.scope(ctx)
	defer done()
	return c.impl.connect(ctx)
}
func (c *Client) Sessions(ctx context.Context) ([]M, error) {
	c.op.Lock()
	defer c.op.Unlock()
	ctx, done := c.scope(ctx)
	defer done()
	return c.impl.sessions(ctx)
}
func (c *Client) Catalog(ctx context.Context) ([]M, error) {
	c.op.Lock()
	defer c.op.Unlock()
	ctx, done := c.scope(ctx)
	defer done()
	if b, ok := c.impl.(*codex); ok {
		return b.catalog(ctx)
	}
	return []M{}, nil
}
func (c *Client) Options() M {
	c.op.Lock()
	defer c.op.Unlock()
	if b, ok := c.impl.(*deepseek); ok {
		return b.options
	}
	return M{}
}
func (c *Client) Open(ctx context.Context, id, title string) (string, error) {
	c.op.Lock()
	defer c.op.Unlock()
	ctx, done := c.scope(ctx)
	defer done()
	sid, e := c.impl.open(ctx, id, title)
	if e == nil && sid == "" {
		e = errors.New("供应商没有返回会话 ID")
	}
	if e == nil {
		c.mu.Lock()
		c.session = sid
		c.mu.Unlock()
	}
	return sid, e
}
func (c *Client) Close() { c.stop(); c.op.Lock(); defer c.op.Unlock(); c.impl.close() }
func (c *Client) stage(ctx context.Context, mid string, files []string) ([]material, error) {
	return (library{root: c.Workspace(), scope: mid}).stageAll(ctx, files)
}
func (c *Client) Send(ctx context.Context, mid, text string, files []string, frame string, meeting M, delta func(string), tool Tool) (string, error) {
	staged, e := c.stage(ctx, mid, files)
	if e != nil {
		return "", e
	}
	return c.send(ctx, mid, text, staged, frame, meeting, delta, tool)
}
func (c *Client) send(ctx context.Context, mid, text string, files []material, frame string, meeting M, delta func(string), tool Tool) (string, error) {
	c.op.Lock()
	defer c.op.Unlock()
	ctx, done := c.scope(ctx)
	defer done()
	id := c.SessionID()
	if id == "" {
		return "", errors.New("请先创建或选择 AI 会话")
	}
	lib := library{root: c.Workspace(), scope: mid}
	inputs, e := lib.blocks(c.cfg.Provider, text, files, frame, meeting)
	if e != nil {
		return "", e
	}
	if delta == nil {
		delta = func(string) {}
	}
	return c.impl.send(ctx, id, inputs, delta, func(name string, args M) (any, error) {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		switch name {
		case "list_attachments", "read_attachment":
			return lib.tool(name, args)
		case "inspect_office":
			return inspectOffice(lib.root, args)
		}
		if tool == nil {
			return nil, errors.New("本场无法执行操作")
		}
		return tool(name, args)
	})
}

//go:embed toolkit.json
var kitJSON []byte
var kit struct {
	Guidance, Local, Codex string
	Schema                 M
	Tools                  []M
	Office                 M
}

func init() {
	if e := json.Unmarshal(kitJSON, &kit); e != nil {
		panic(e)
	}
}
func str(a any) string { s, _ := a.(string); return s }
func obj(a any) M      { v, _ := a.(map[string]any); return v }
func list(a any) []any { v, _ := a.([]any); return v }
func rows(a any) []M {
	r := []M{}
	for _, v := range list(a) {
		if x := obj(v); x != nil {
			r = append(r, x)
		}
	}
	return r
}
func encode(a any) string { b, _ := json.Marshal(a); return string(b) }
func integer(a any) int {
	switch n := a.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}
func first(values ...string) string {
	for _, s := range values {
		if s != "" {
			return s
		}
	}
	return ""
}
func cut(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		r = r[:n]
	}
	return string(r)
}
func specs(local, office bool) []M {
	out := []M{}
	for _, s := range kit.Tools {
		if local && (s["name"] == "read_attachment" || s["name"] == "list_attachments") {
			continue
		}
		out = append(out, s)
	}
	if office {
		out = append(out, kit.Office)
	}
	return out
}
func invoke(t Tool, name string, raw any, local, office bool) (out string) {
	fail := func(err string) string { return encode(M{"error": err}) }
	var a M
	switch v := raw.(type) {
	case string:
		if json.Unmarshal([]byte(v), &a) != nil {
			return fail("工具参数须为 JSON 对象")
		}
	case map[string]any:
		a = v
	}
	if a == nil || t == nil {
		return fail("工具不可用")
	}
	for _, s := range specs(local, office) {
		if s["name"] != name {
			continue
		}
		fields := obj(obj(s["parameters"])["properties"])
		if slices.Contains([]string{"read_transcript", "list_topic_meetings", "read_topic_history", "list_topic_notes"}, name) {
			for k, v := range (M{"offset": 0, "limit": 30, "query": ""}) {
				if _, ok := a[k]; !ok {
					a[k] = v
				}
			}
		}
		if len(a) != len(fields) {
			return fail("工具参数有缺失或额外字段")
		}
		for k, v := range fields {
			f := obj(v)
			switch f["type"] {
			case "integer":
				switch n := a[k].(type) {
				case float64:
					if n != float64(int(n)) {
						return fail(k + " 须为整数")
					}
					a[k] = int(n)
				case int:
				default:
					return fail(k + " 须为整数")
				}
			case "string":
				x, ok := a[k].(string)
				if !ok {
					return fail(k + " 须为文字")
				}
				if vals := list(f["enum"]); len(vals) > 0 && !slices.Contains(vals, any(x)) {
					return fail(k + " 值无效")
				}
			}
		}
		result, e := t(name, a)
		if e != nil {
			return fail(e.Error())
		}
		return encode(M{"result": result})
	}
	return fail("未知的会议工具")
}
func mcp(p M, t Tool, office bool) M {
	id, ok := p["id"]
	if !ok {
		return nil
	}
	r := M{"id": id, "jsonrpc": "2.0"}
	a := obj(p["params"])
	switch str(p["method"]) {
	case "initialize":
		v := str(a["protocolVersion"])
		if !slices.Contains([]string{"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}, v) {
			v = "2025-11-25"
		}
		r["result"] = M{"protocolVersion": v, "capabilities": M{"tools": M{}}, "serverInfo": M{"name": "tingsu-meeting", "version": "2"}}
	case "ping":
		r["result"] = M{}
	case "tools/list":
		l := []M{}
		if t != nil {
			for _, s := range specs(true, office) {
				l = append(l, M{"name": s["name"], "description": s["description"], "inputSchema": s["parameters"]})
			}
		}
		r["result"] = M{"tools": l}
	case "tools/call":
		o := invoke(t, str(a["name"]), a["arguments"], true, office)
		var result M
		_ = json.Unmarshal([]byte(o), &result)
		r["result"] = M{"content": []M{{"type": "text", "text": o}}, "isError": result["error"] != nil}
	default:
		r["error"] = M{"code": -32601, "message": "Method not found"}
	}
	return r
}
func check(e error, msg string) error {
	if e == nil {
		return nil
	}
	return fmt.Errorf("%s: %w", msg, e)
}

// Prepare preserves the original attachments when an independent meeting joins a topic.
func (c *Client) Prepare(ctx context.Context, meetings []string) error {
	for _, mid := range meetings {
		original := filepath.Join(c.cfg.DataDir, "workspaces", digest("meeting:" + mid)[:24])
		if original == c.Workspace() {
			continue
		}
		from, to := library{original, mid}, library{c.Workspace(), mid}
		dirs, e := os.ReadDir(from.dir())
		if os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return e
		}
		for _, dir := range dirs {
			if e := ctx.Err(); e != nil {
				return e
			}
			if !dir.IsDir() || !digestRE.MatchString(dir.Name()) {
				continue
			}
			if _, e = to.get(dir.Name()); e == nil {
				continue
			}
			m, e := from.get(dir.Name())
			if e != nil {
				return e
			}
			if _, e = to.stage(ctx, m.path); e != nil {
				return e
			}
		}
	}
	return nil
}
