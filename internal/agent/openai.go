package agent

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"slices"
	"strings"
	"time"
)

type openAI struct {
	base, key, model, account string
	history                   History
	http                      *http.Client
}

func newOpenAI(c Config) (*openAI, error) {
	base := strings.TrimRight(first(os.Getenv("OPENAI_BASE_URL"), "https://api.openai.com/v1"), "/")
	u, e := url.Parse(base)
	if e != nil || u.User != nil || u.Host == "" || (u.Scheme != "https" && !(u.Scheme == "http" && slices.Contains([]string{"127.0.0.1", "localhost", "::1"}, u.Hostname()))) {
		return nil, errors.New("OpenAI API 地址需要 HTTPS，本机服务可使用回环地址")
	}
	key := first(c.Key, os.Getenv("OPENAI_API_KEY"))
	return &openAI{base, key, first(c.Model, "gpt-5-mini"), digest(base + "\n" + key)[:24], c.History, &http.Client{Timeout: 180 * time.Second}}, nil
}
func (b *openAI) req(ctx context.Context, path string, payload M) (io.ReadCloser, error) {
	method := "GET"
	var body io.Reader
	if payload != nil {
		method = "POST"
		body = strings.NewReader(encode(payload))
	}
	req, e := http.NewRequestWithContext(ctx, method, b.base+"/"+path, body)
	if e != nil {
		return nil, e
	}
	req.Header.Set("Authorization", "Bearer "+b.key)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	res, e := b.http.Do(req)
	if e != nil {
		return nil, e
	}
	if res.StatusCode/100 != 2 {
		defer res.Body.Close()
		var m M
		_ = json.NewDecoder(io.LimitReader(res.Body, 1<<16)).Decode(&m)
		detail := str(obj(m["error"])["message"])
		if b.key != "" {
			detail = strings.ReplaceAll(detail, b.key, "•••")
		}
		return nil, fmt.Errorf("OpenAI HTTP %d：%s", res.StatusCode, cut(detail, 500))
	}
	return res.Body, nil
}
func (b *openAI) get(ctx context.Context, path string, p M) (M, error) {
	r, e := b.req(ctx, path, p)
	if e != nil {
		return nil, e
	}
	defer r.Close()
	var m M
	e = json.NewDecoder(io.LimitReader(r, 16<<20)).Decode(&m)
	return m, e
}
func (b *openAI) connect(ctx context.Context) error {
	if b.key == "" {
		return errors.New("请在设置中填写 OpenAI API Key 或设置 OPENAI_API_KEY")
	}
	_, e := b.get(ctx, "models/"+url.PathEscape(b.model), nil)
	return e
}
func (b *openAI) sessions(context.Context) ([]M, error) {
	if b.history != nil {
		return b.history.OpenAISessions(b.account), nil
	}
	return []M{}, nil
}

var convRE = regexp.MustCompile("^conv_[A-Za-z0-9_-]+$")

func (b *openAI) open(ctx context.Context, id, title string) (string, error) {
	path := "conversations"
	var p M
	if id != "" {
		if !convRE.MatchString(id) {
			return "", errors.New("请输入 conv_ 会话 ID")
		}
		path += "/" + id
	} else {
		p = M{"metadata": M{"title": cut(title, 500)}}
	}
	r, e := b.get(ctx, path, p)
	if e != nil {
		return "", e
	}
	id = str(r["id"])
	if !convRE.MatchString(id) {
		return "", errors.New("服务返回无效会话 ID")
	}
	if b.history != nil {
		e = b.history.RememberOpenAI(b.account, id, first(str(obj(r["metadata"])["title"]), title))
	}
	return id, e
}
func (b *openAI) save(id string, c map[string]string) error {
	if b.history == nil {
		return nil
	}
	return b.history.SaveOpenAIPending(b.account, id, c)
}
func (b *openAI) send(ctx context.Context, id string, in []M, delta func(string), tool Tool) (string, error) {
	items := []M{}
	cached := map[string]string{}
	if b.history != nil {
		pending, e := b.history.OpenAIPending(b.account, id)
		if e != nil {
			return "", e
		}
		if pending != nil {
			cached = pending
			page, e := b.get(ctx, "conversations/"+id+"/items?order=desc&limit=100", nil)
			if e != nil {
				return "", e
			}
			seen := map[string]bool{}
			for _, r := range rows(page["data"]) {
				if r["role"] == "user" {
					break
				}
				cid := str(r["call_id"])
				if r["type"] == "function_call_output" {
					seen[cid] = true
				}
				if r["type"] == "function_call" && !seen[cid] {
					items = append(items, M{"type": "function_call_output", "call_id": cid, "output": first(cached[cid], encode(M{"error": "上次请求中断，请先检查当前会议状态。"}))})
				}
			}
			slices.Reverse(items)
		}
	}
	items = append(items, M{"role": "user", "content": in})
	if e := b.save(id, cached); e != nil {
		return "", e
	}
	messages := []string{}
	for range 8 {
		r, e := b.req(ctx, "responses", M{"model": b.model, "conversation": id, "instructions": kit.Guidance, "input": items, "stream": true, "tools": kit.Tools, "parallel_tool_calls": false})
		if e != nil {
			return "", e
		}
		var msg strings.Builder
		completed, e := scanSSE(r, func(s string) {
			if len(messages) > 0 && msg.Len() == 0 {
				delta("\n")
			}
			msg.WriteString(s)
			delta(s)
		})
		r.Close()
		if e != nil {
			return "", e
		}
		if msg.Len() > 0 {
			messages = append(messages, msg.String())
		}
		items = nil
		for _, v := range rows(completed["output"]) {
			if v["type"] != "function_call" {
				continue
			}
			cid := str(v["call_id"])
			out, ok := cached[cid]
			if !ok {
				out = invoke(tool, str(v["name"]), v["arguments"], false, false)
				cached[cid] = out
				if e := b.save(id, cached); e != nil {
					return "", e
				}
			}
			items = append(items, M{"type": "function_call_output", "call_id": cid, "output": out})
		}
		if len(items) == 0 {
			return strings.Join(messages, "\n"), b.save(id, nil)
		}
	}
	return "", errors.New("已完成八轮工具调用，请继续提问")
}
func scanSSE(r io.Reader, delta func(string)) (M, error) {
	s := bufio.NewScanner(r)
	s.Buffer(make([]byte, 8192), 32<<20)
	for s.Scan() {
		line, ok := strings.CutPrefix(s.Text(), "data:")
		if !ok {
			continue
		}
		line = strings.TrimSpace(line)
		if line == "" || line == "[DONE]" {
			continue
		}
		var e M
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			return nil, err
		}
		switch e["type"] {
		case "response.output_text.delta", "response.refusal.delta":
			delta(str(e["delta"]))
		case "response.completed":
			return obj(e["response"]), nil
		case "error", "response.failed", "response.incomplete":
			return nil, fmt.Errorf("OpenAI：%s", cut(first(str(obj(obj(e["response"])["error"])["message"]), str(e["message"]), str(e["type"])), 500))
		}
	}
	if e := s.Err(); e != nil {
		return nil, e
	}
	return nil, errors.New("OpenAI 流式连接提前结束")
}
func (b *openAI) close() { b.http.CloseIdleConnections() }
