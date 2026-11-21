package agent

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/zzttzzmyswy/tingsu/internal/store"
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

func TestRepairInterruptedOpenAITool(t *testing.T) {
	var calls int
	var replies []M
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/items") {
			fmt.Fprint(w, encode(M{"data": []M{{"type": "function_call", "call_id": "persistent"}, {"type": "message", "role": "user"}}}))
			return
		}
		if r.URL.Path != "/v1/responses" {
			fmt.Fprint(w, encode(M{"id": "conv_repair", "metadata": M{"title": "会议"}}))
			return
		}
		var req M
		json.NewDecoder(r.Body).Decode(&req)
		replies = rows(req["input"])
		calls++
		if calls == 1 {
			fmt.Fprint(w, "data: "+encode(M{"type": "response.completed", "response": M{"output": []M{{"type": "function_call", "name": "create_note", "arguments": encode(M{"kind": "idea", "title": "保留", "body": ""}), "call_id": "persistent"}}}})+"\n\n")
			return
		}
		if calls == 2 {
			return
		}
		fmt.Fprint(w, "data: "+encode(M{"type": "response.output_text.delta", "delta": "完成"})+"\n\ndata: "+encode(M{"type": "response.completed", "response": M{"output": []M{}}})+"\n\n")
	}))
	defer server.Close()
	t.Setenv("OPENAI_BASE_URL", server.URL+"/v1")
	db, e := store.Open(filepath.Join(t.TempDir(), "meetings.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	cfg := Config{Provider: "openai", DataDir: t.TempDir(), Key: "fixture", History: db}
	ctx := context.Background()
	connect := func(id string) *Client {
		c, e := New(cfg)
		if e != nil {
			t.Fatal(e)
		}
		t.Cleanup(c.Close)
		if e = c.Connect(ctx); e != nil {
			t.Fatal(e)
		}
		if _, e = c.Open(ctx, id, "会议"); e != nil {
			t.Fatal(e)
		}
		return c
	}
	mutations := 0
	tool := func(name string, args M) (any, error) { mutations++; return M{"id": "stable-note-id"}, nil }
	c := connect("")
	if _, e = c.Send(ctx, "m", "创建便签", nil, "", nil, nil, tool); e == nil {
		t.Fatal("expected interrupted response")
	}
	c.Close()
	c = connect("conv_repair")
	sessions, e := c.Sessions(ctx)
	if e != nil || len(sessions) != 1 {
		t.Fatal(sessions, e)
	}
	reply, e := c.Send(ctx, "m", "继续", nil, "", nil, nil, tool)
	if e != nil || reply != "完成" || mutations != 1 || len(replies) != 2 || replies[0]["type"] != "function_call_output" || !strings.Contains(str(replies[0]["output"]), "stable-note-id") {
		t.Fatal(reply, e, mutations, replies)
	}
}
func TestTopicWorkspaceCopiesAssociatedOriginal(t *testing.T) {
	data := t.TempDir()
	mid := "standalone"
	orig := library{filepath.Join(data, "workspaces", digest("meeting:" + mid)[:24]), mid}
	file := filepath.Join(t.TempDir(), "议程.txt")
	if e := os.WriteFile(file, []byte("原件"), 0600); e != nil {
		t.Fatal(e)
	}
	m, e := orig.stage(context.Background(), file)
	if e != nil {
		t.Fatal(e)
	}
	os.Remove(file)
	cfg := Config{Provider: "codex", DataDir: data, Workspace: filepath.Join(data, "workspaces", "topic")}
	c, e := New(cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	for range 2 {
		if e = c.Prepare(context.Background(), []string{mid, "new"}); e != nil {
			t.Fatal(e)
		}
	}
	got, e := (library{c.Workspace(), mid}).get(str(m.info["id"]))
	if e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(got.path)
	if string(b) != "原件" {
		t.Fatal(got)
	}
}
func TestOfficeInspectPagination(t *testing.T) {
	root := t.TempDir()
	f, e := os.Create(filepath.Join(root, "会议.xlsx"))
	if e != nil {
		t.Fatal(e)
	}
	w := zip.NewWriter(f)
	for p, s := range map[string]string{
		"xl/workbook.xml":            `<workbook xmlns:r="urn:rels"><sheets><sheet name="预算" r:id="a"/></sheets></workbook>`,
		"xl/_rels/workbook.xml.rels": `<Relationships><Relationship Id="a" Target="worksheets/sheet3.xml"/><Relationship Id="b" Target="sharedStrings.xml" Type="urn:test/sharedStrings"/></Relationships>`,
		"xl/worksheets/sheet3.xml":   `<sheet><row><c r="C2" t="s"><v>1</v></c><c r="D2"><f>1+1</f><v>2</v></c></row></sheet>`,
		"xl/sharedStrings.xml":       `<strings><si><t>标题</t></si><si><t>开销</t></si></strings>`} {
		o, e := w.Create(p)
		if e != nil {
			t.Fatal(e)
		}
		fmt.Fprint(o, s)
	}
	if e = w.Close(); e != nil {
		t.Fatal(e)
	}
	if e = f.Close(); e != nil {
		t.Fatal(e)
	}
	a := M{"path": "会议.xlsx", "section": "预算", "offset": 0, "limit": 1}
	read, e := inspectOffice(root, a)
	if e != nil {
		t.Fatal(e)
	}
	page := obj(read)
	if page["next_offset"] != 1 || page["items"].([]M)[0]["value"] != "开销" {
		t.Fatal(page)
	}
	a["offset"] = 1
	read, e = inspectOffice(root, a)
	if e != nil {
		t.Fatal(e)
	}
	if obj(read)["items"].([]M)[0]["formula"] != "1+1" {
		t.Fatal(read)
	}
}
