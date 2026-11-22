package desktop

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/zzttzzmyswy/tingsu/internal/store"
)

func TestBoardScopeKeepsSourceAndSharedEdits(t *testing.T) {
	local := []M{{"id": "a", "kind": "idea"}, {"id": "b", "kind": "task"}}
	shared := []M{{"id": "c", "source_note": "b", "kind": "task", "status": "done"}, {"id": "d", "source_meeting": "prior", "kind": "task", "status": "open"}}
	for _, tc := range []struct {
		scope, kind, status string
		ids                 []string
	}{
		{"", "", "", []string{"c", "d", "a"}}, {"meeting", "", "", []string{"a", "b"}},
		{"topic", "task", "pending", []string{"d"}}, {"topic", "", "done", []string{"c"}},
	} {
		var ids []string
		for _, n := range boardCards(local, shared, tc.scope, tc.kind, tc.status) {
			ids = append(ids, str(n["id"]))
		}
		if !slices.Equal(ids, tc.ids) {
			t.Errorf("%+v: got %v", tc, ids)
		}
	}
}

func TestExportUsesFullSelectedMeeting(t *testing.T) {
	db, err := store.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	mid, err := db.Create("产品评审", "", "", 0, "", "每周评审")
	if err != nil {
		t.Fatal(err)
	}
	for i := range 130 {
		if err = db.Event(mid, "speech", float64(i), float64(i), "林悦", "这是原话 **照录**", nil); err != nil {
			t.Fatal(err)
		}
	}
	if err = db.Event(mid, "assistant", 131, 131, "助手", "## 决定\n\n**保留原型**", []string{"设计.pdf"}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var dialog M
	h := &host{ctx: ctx, in: make(chan struct{}, 1), dialogs: map[int]func(any){}, deliver: func(m M) { dialog = m }}
	b := &bridge{host: h, db: db, mid: mid}
	target := filepath.Join(t.TempDir(), "会议.html")
	if err = b.command("export", M{"format": "html", "meeting": mid}); err != nil {
		t.Fatal(err)
	}
	if dialog["kind"] != "save-file" || dialog["filter"] != "*.html" {
		t.Fatalf("unexpected dialog %v", dialog)
	}
	other, err := db.Create("另一场会议", "", "", 0, "", "")
	if err != nil {
		t.Fatal(err)
	}
	b.mid = other
	b.dialogs[int(number(dialog["id"]))](target)
	select {
	case <-h.in:
		h.drain()
	case <-time.After(5 * time.Second):
		t.Fatal("export did not finish")
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	s := string(data)
	if strings.Count(s, "这是原话 **照录**") != 130 || !strings.Contains(s, "<strong>保留原型</strong>") || !strings.Contains(s, "设计.pdf") {
		t.Fatal("incomplete export or incorrect Markdown scope")
	}
}

func TestExportFormatsAndFailedOverwrite(t *testing.T) {
	e := exportEntry{title: "未标时 · 听序 AI", markdown: true, body: "## 结论\n\n- **上线**\n\n![预览](https://example.org/image.jpg)\n\n<script>unsafe()</script>"}
	d := exportDocument{title: "评审 <草案>", groups: []exportGroup{{name: "会议记录", entries: []exportEntry{e}}}}
	for _, format := range []string{"txt", "html", "pdf", "md"} {
		var out bytes.Buffer
		if err := d.write(&out, format); err != nil {
			t.Fatal(err)
		}
		s := out.String()
		switch format {
		case "txt":
			if strings.Contains(s, "**") || !strings.Contains(s, "上线") {
				t.Error("TXT contains Markdown or lost text")
			}
		case "html":
			if strings.Contains(s, "<script") || strings.Contains(s, "<img") || !strings.Contains(s, "&lt;草案&gt;") || !strings.Contains(s, "<li>") {
				t.Error("unsafe or unformatted HTML")
			}
		case "pdf":
			if !strings.HasPrefix(s, "%PDF-") || !strings.Contains(s, "/FontFile2") || out.Len() > 5<<20 {
				t.Error("expected a PDF with an embedded TrueType font")
			}
		case "md":
			if !strings.Contains(s, e.body) {
				t.Error("lost Markdown source")
			}
		}
	}
	path := filepath.Join(t.TempDir(), "previous.txt")
	if err := os.WriteFile(path, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := saveExport(path, "unsupported", d); err == nil {
		t.Fatal("unexpected export success")
	}
	after, err := os.ReadFile(path)
	if err != nil || string(after) != "existing" {
		t.Fatal("failed export damaged previous file")
	}
}

func TestMarkdownShowsCodeAndKeepsExternalResourcesInert(t *testing.T) {
	src := "3. 开始\n4. 确认\n\n> 原话\n\n```go\nvar x = 2\n```\n\n| 项目 | 状态 |\n|---|---|\n| A | 完成 |\n\n[来源](https://example.org/notes)\n\n<file:///tmp/secret>\n\n![图](https://example.org/pixel)"
	rendered, err := markdownHTML(src)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"<code", "var x = 2", "<table>", "<blockquote>", "https://example.org/notes"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("missing %s", want)
		}
	}
	if strings.Contains(rendered, "<img") || strings.Contains(rendered, `href="file:`) {
		t.Fatal("active local or remote resource")
	}
	var plain strings.Builder
	for _, span := range markdownSpans(rendered, markdownTheme{}) {
		plain.WriteString(span.Content)
	}
	for _, want := range []string{"3. 开始", "4. 确认", "var x = 2", "https://example.org/notes", "完成"} {
		if !strings.Contains(plain.String(), want) {
			t.Errorf("lost %q in %q", want, plain.String())
		}
	}
}
