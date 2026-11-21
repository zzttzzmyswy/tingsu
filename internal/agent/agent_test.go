package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestTurnQueueCancelAndDetach(t *testing.T) {
	partial := make(chan struct{}, 1)
	done := make(chan struct{}, 4)
	var mu sync.Mutex
	var assistant []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" {
			fmt.Fprint(w, `{"id":"conv_test"}`)
			return
		}
		var m M
		json.NewDecoder(r.Body).Decode(&m)
		v := rows(rows(m["input"])[0]["content"])
		var req M
		json.Unmarshal([]byte(str(v[0]["text"])), &req)
		msg := str(req["request"])
		fmt.Fprint(w, "data: "+encode(M{"type": "response.output_text.delta", "delta": msg})+"\n\n")
		w.(http.Flusher).Flush()
		if msg == "一" {
			<-r.Context().Done()
			return
		}
		fmt.Fprint(w, "data: "+encode(M{"type": "response.completed", "response": M{"output": []M{}}})+"\n\n")
	}))
	defer server.Close()
	t.Setenv("OPENAI_BASE_URL", server.URL+"/v1")
	c, e := New(Config{Provider: "openai", Key: "fixture", DataDir: t.TempDir()})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	ctx := context.Background()
	if e = c.Connect(ctx); e != nil {
		t.Fatal(e)
	}
	if _, e = c.Open(ctx, "", "新会话"); e != nil {
		t.Fatal(e)
	}
	h := Hooks{Position: func(string) float64 { return 1 }, Context: func(string) (M, error) { return M{}, nil }, Event: func(mid, k string, _ float64, _, text string, _ []string) error {
		if k == "assistant" {
			mu.Lock()
			assistant = append(assistant, text)
			mu.Unlock()
			done <- struct{}{}
		}
		return nil
	}, Stream: func(v string) {
		if v == "一" {
			partial <- struct{}{}
		}
	}}
	s := NewScheduler(h, false)
	defer s.Detach()
	s.Bind("m1", c, 0)
	if e = s.Submit("一", nil); e != nil {
		t.Fatal(e)
	}
	select {
	case <-partial:
	case <-time.After(5 * time.Second):
		t.Fatal("no partial reply")
	}
	for _, p := range []string{"二", "三"} {
		if e = s.Submit(p, nil); e != nil {
			t.Fatal(e)
		}
	}
	s.Cancel()
	for range 3 {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Fatal("no final reply")
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if !reflect.DeepEqual(assistant, []string{"一\n（回复已停止）", "二", "三"}) {
		t.Fatal(assistant)
	}
}
func TestWorkspaceOriginal(t *testing.T) {
	root := t.TempDir()
	l := library{root, "m1"}
	p := filepath.Join(t.TempDir(), "议程.txt")
	err := os.WriteFile(p, []byte("ORCHID-718"), 0600)
	if err != nil {
		t.Fatal(err)
	}
	input, e := l.inputs(context.Background(), "codex", "分析附件", []string{p}, "", M{})
	if e != nil {
		t.Fatal(e)
	}
	var request M
	_ = json.Unmarshal([]byte(str(input[0]["text"])), &request)
	v := rows(request["attachments"])[0]
	if strings.Contains(encode(input), "ORCHID") || !strings.HasPrefix(str(v["path"]), "attachments/") {
		t.Fatal(v)
	}
	b, e := os.ReadFile(filepath.Join(root, str(v["path"])))
	if e != nil || string(b) != "ORCHID-718" {
		t.Fatal(b, e)
	}
	_, e = (library{root, "m2"}).get(str(v["id"]))
	if e == nil {
		t.Fatal("cross-meeting attachment access")
	}
	r, e := l.tool("read_attachment", M{"id": v["id"], "offset": -3, "limit": 5, "format": "text"})
	if e != nil || obj(r)["content"] != "718" {
		t.Fatal(r, e)
	}
}

// An opt-in integration case run with each installed CLI and the repository's loopback model fixtures.
// It exercises the actual process, file tools, meeting tools, session persistence and resume.
func TestInstalledAgent(t *testing.T) {
	provider := os.Getenv("TINGSU_TEST_PROVIDER")
	if provider == "" {
		t.Skip("requires a local CLI and matching loopback model fixture")
	}
	root := t.TempDir()
	cfg := Config{Provider: provider, Executable: os.Getenv("TINGSU_TEST_EXECUTABLE"), Workspace: filepath.Join(root, "会议工作目录"), DataDir: root}
	if provider == "codex" {
		cfg.Model = "gpt-5"
		cfg.Effort = "high"
		cfg.Speed = "priority"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	start := func() *Client {
		c, e := New(cfg)
		if e != nil {
			t.Fatal(e)
		}
		t.Cleanup(c.Close)
		if e = c.Connect(ctx); e != nil {
			t.Fatal(e)
		}
		if _, e = c.Sessions(ctx); e != nil {
			t.Fatal(e)
		}
		return c
	}
	c := start()
	sid, e := c.Open(ctx, "", "本地 CLI 验收")
	if e != nil {
		t.Fatal(e)
	}
	file := filepath.Join(root, "agenda.txt")
	_ = os.WriteFile(file, []byte("合成会议材料 ORCHID-718。"), 0600)
	notes := 0
	tool := func(name string, a M) (any, error) {
		switch name {
		case "read_transcript":
			return []M{{"text": "准备检查附件。"}}, nil
		case "create_note":
			if a["body"] != "ORCHID-718" {
				return nil, errors.New("incorrect marker")
			}
			notes++
			return M{"id": "note"}, nil
		}
		return nil, fmt.Errorf("unexpected tool: %s", name)
	}
	prompt := "请阅读附件，创建行动项"
	if provider == "codex" {
		prompt = "ORCHID-718"
	}
	var stream strings.Builder
	reply, e := c.Send(ctx, "m1", prompt, []string{file}, "", M{}, func(v string) { stream.WriteString(v) }, tool)
	if e != nil {
		t.Fatal(e)
	}
	if reply == "" || stream.Len() == 0 {
		t.Fatal("empty reply")
	}
	if provider != "codex" && notes != 1 {
		t.Fatalf("expected one tool mutation, got %d (reply %q)", notes, reply)
	}
	c.Close()
	restored := start()
	sessions, e := restored.Sessions(ctx)
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, s := range sessions {
		found = found || s["id"] == sid
	}
	if !found {
		t.Fatalf("session missing from history (%d sessions)", len(sessions))
	}
	if id, e := restored.Open(ctx, sid, ""); id != sid || e != nil {
		t.Fatal(id, e)
	}
	t.Logf("%s: workspace file -> %d mutations -> streamed reply -> resume OK", provider, notes)
}
