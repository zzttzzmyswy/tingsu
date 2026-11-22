package store

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestAgentInputCoverageResumeAndIsolation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "meetings.db")
	db, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	mid, err := db.Create("回放", "", "", 0, "", "")
	if err != nil {
		t.Fatal(err)
	}
	var want strings.Builder
	for i := range 145 {
		text := "发言🐟。"
		if i == 2 {
			text = strings.Repeat("长句🐟", 16001)
		}
		want.WriteString(text)
		if err := db.Event(mid, "speech", float64(145-i), float64(146-i), "人", text, nil); err != nil {
			t.Fatal(err)
		}
	}
	card, err := db.Tool(mid, "create_note", Row{"title": "待核实", "kind": "question", "body": "甲"}, "我")
	if err != nil {
		t.Fatal(err)
	}
	input := func(sid string) AgentBatch {
		t.Helper()
		b, e := db.AgentInput(mid, "codex", sid)
		if e != nil {
			t.Fatal(e)
		}
		return b
	}
	first := input("one")
	if !reflect.DeepEqual(first.Context, input("one").Context) {
		t.Fatal("unacknowledged data advanced")
	}
	var got strings.Builder
	b := first
	for {
		for _, row := range b.Context["新增发言"].([]Row) {
			got.WriteString(str(row["text"]))
		}
		if err := b.Acknowledge(); err != nil {
			t.Fatal(err)
		}
		if !b.More {
			break
		}
		b = input("one")
	}
	if got.String() != want.String() {
		t.Fatal("transcript loss, duplication or reordering")
	}
	if first.Acknowledge() == nil {
		t.Fatal("stale acknowledgement succeeded")
	}
	db.Close()
	db, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if b := input("one"); b.HasSpeech || len(b.Context["当前便签"].([]Row)) != 0 {
		t.Fatal("reconnect replayed delivered data")
	}
	if b := input("two"); !reflect.DeepEqual(first.Context, b.Context) {
		t.Fatal("new session missed data")
	}
	nid := card.(Row)["id"]
	if _, err = db.Tool(mid, "update_note", Row{"id": nid, "title": "已核实", "kind": "idea", "body": "乙"}, "我"); err != nil {
		t.Fatal(err)
	}
	b = input("one")
	if len(b.Context["当前便签"].([]Row)) != 1 {
		t.Fatal("missing edit")
	}
	if err = b.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Tool(mid, "delete_note", Row{"id": nid}, "我"); err != nil {
		t.Fatal(err)
	}
	if len(input("one").Context["已移除"].([]string)) != 1 {
		t.Fatal("missing deletion")
	}
}
