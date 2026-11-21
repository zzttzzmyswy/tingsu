package desktop

import (
	"bufio"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"github.com/zzttzzmyswy/tingsu/internal/agent"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

func (b *bridge) detach() {
	b.generation++
	if b.disconnect != nil {
		b.disconnect()
		b.disconnect = nil
	}
	b.loginURL = ""
	b.turns.Detach()
	if c := b.client; c != nil {
		go c.Close()
	}
	b.client = nil
	b.sessions = []M{}
	b.session = ""
	b.provider = ""
	b.status = "offline"
	b.changed()
}
func (b *bridge) agentConfig(provider string) agent.Config {
	m := b.current()
	scope := "meeting:" + b.mid
	if topic := str(m["topic_id"]); topic != "" {
		scope = "topic:" + topic
	}
	hash := sha256.Sum256([]byte(scope))
	options, _ := b.settings.values["local_agent_options"].(M)
	opt, _ := prefs(options[provider])
	model := str(opt["model"])
	if provider == "openai" {
		model = str(b.settings.values["model"])
	}
	return agent.Config{Provider: provider, Key: b.settings.key("openai"), Model: model, Effort: str(opt["effort"]), Speed: str(opt["speed"]), Executable: str(b.settings.values[provider+"_path"]), DataDir: b.data, Workspace: filepath.Join(b.data, "workspaces", fmt.Sprintf("%x", hash[:12])), History: b.db}
}
func (b *bridge) connect(a M) error {
	if busy, q := b.turns.State(); busy || q > 0 {
		return errors.New("AI 正在回复")
	}
	b.detach()
	m := b.current()
	p := str(a["provider"])
	if p == "" {
		p = str(m["topic_provider"])
	}
	if p == "" {
		p = str(b.settings.values["agent"])
	}
	if providers[p] == nil {
		return errors.New("未知 AI 服务")
	}
	if raw, ok := a["options"]; ok && p != "openai" {
		opt, e := prefs(raw)
		if e != nil {
			return e
		}
		old, _ := b.settings.values["local_agent_options"].(M)
		next := M{}
		for k, v := range old {
			next[k] = v
		}
		next[p] = opt
		if e = b.settings.save(M{"local_agent_options": next}); e != nil {
			return e
		}
	}
	config := b.agentConfig(p)
	c, e := agent.New(config)
	if e != nil {
		return e
	}
	b.client = c
	b.provider = p
	b.status = "connecting"
	b.changed()
	g, mid := b.generation, b.mid
	ctx, cancel := context.WithCancel(b.ctx)
	b.disconnect = cancel
	go func() {
		e := c.Connect(ctx)
		sessions := []M{}
		if e == nil && str(m["topic_id"]) == "" {
			var all []M
			all, e = c.Sessions(ctx)
			blocked := b.db.UnavailableSessionIDs(mid, p)
			for _, s := range all {
				if !blocked[str(s["id"])] {
					sessions = append(sessions, s)
				}
			}
		}
		b.post(func() {
			if g != b.generation {
				return
			}
			if e != nil {
				b.detach()
				b.fail(e)
				return
			}
			b.sessions = sessions
			b.status = "choose"
			b.changed()
			if str(m["topic_id"]) != "" {
				if saved := b.db.AgentSessions(mid)[p]; saved != "" {
					if e := b.openSession(M{"id": saved}); e != nil {
						b.fail(e)
					}
					return
				}
			}
			b.publish()
			b.event("choose", "")
		})
	}()
	return nil
}
func (b *bridge) openSession(a M) error {
	if b.status != "choose" || b.client == nil {
		return errors.New("请先连接 AI 服务")
	}
	c, mid, p, g := b.client, b.mid, b.provider, b.generation
	m := b.current()
	req := str(a["id"])
	var expected *string
	title := str(m["title"])
	if str(m["topic_id"]) != "" {
		title = str(m["topic_name"])
		saved := b.db.AgentSessions(mid)[p]
		expected = &saved
		if req != "" && req != saved {
			return errors.New("请选择本主题自己的 AI 会话。")
		}
		if saved != "" && req == "" && !truth(a["replace"]) {
			return errors.New("本主题已有会话，请继续原会话或选择重新建立。")
		}
	}
	if e := b.db.CheckAgentSession(mid, p, req); e != nil {
		return e
	}
	b.status = "connecting"
	b.changed()
	go func() {
		ctx, cancel := context.WithTimeout(b.ctx, 120*time.Second)
		defer cancel()
		id, e := c.Open(ctx, req, title)
		b.post(func() {
			if g != b.generation {
				return
			}
			if expected != nil && req != "" && id != req && e == nil {
				e = errors.New("服务返回了不同的会话，请重试。")
			}
			if e == nil {
				e = b.db.BindAgent(mid, p, id, expected)
			}
			if e != nil {
				b.status = "choose"
				b.fail(e)
				b.publish()
				b.event("choose", "")
				return
			}
			b.session = id
			b.status = "ready"
			if p == "deepseek" {
				b.catalogs[p] = c.Options()
			}
			b.setFrameRequest()
			b.turns.Bind(mid, c, number(b.current()["duration"]))
			b.publish()
			b.event("connected", "")
			b.notice("AI 会话已连接", "ok")
		})
	}()
	return nil
}
func (b *bridge) loadCatalog(path string) {
	cfg := b.agentConfig("codex")
	if path != "" {
		cfg.Executable = path
	}
	go func() {
		ctx, cancel := context.WithTimeout(b.ctx, time.Minute)
		defer cancel()
		c, e := agent.New(cfg)
		var rows []M
		if e == nil {
			defer c.Close()
			e = c.Connect(ctx)
		}
		if e == nil {
			rows, e = c.Catalog(ctx)
		}
		b.post(func() {
			if e != nil {
				b.fail(e)
				return
			}
			b.catalogs["codex"] = rows
			b.publish()
			b.event("agent-catalog", "codex")
		})
	}()
}

var loginLink = regexp.MustCompile("https://auth[.]openai[.]com/[a-zA-Z0-9/?%&._=:+~-]+")

func (b *bridge) login() error {
	if b.status != "offline" {
		return errors.New("请先断开当前会话")
	}
	ctx, cancel := context.WithTimeout(b.ctx, 10*time.Minute)
	c, e := agent.Command(ctx, b.agentConfig("codex"), "login")
	if e != nil {
		cancel()
		return e
	}
	read, write, e := os.Pipe()
	if e != nil {
		cancel()
		return e
	}
	c.Stdout = write
	c.Stderr = write
	if e = c.Start(); e != nil {
		cancel()
		read.Close()
		write.Close()
		return e
	}
	write.Close()
	b.disconnect = cancel
	b.status = "login"
	g := b.generation
	go func() {
		defer read.Close()
		scan := bufio.NewScanner(io.LimitReader(read, 256<<10))
		for scan.Scan() {
			if link := loginLink.FindString(scan.Text()); link != "" {
				b.post(func() {
					if b.generation == g {
						b.loginURL = link
						b.changed()
					}
				})
			}
		}
		e := c.Wait()
		b.post(func() {
			if g != b.generation {
				return
			}
			b.detach()
			if e != nil {
				b.fail(errors.New("Codex 登录未完成，可以重新登录。"))
			} else if e = b.connect(M{"provider": "codex"}); e != nil {
				b.fail(e)
			}
		})
	}()
	return nil
}
