package store

import (
	"path/filepath"
	"testing"
)

func TestTopicContinuityAndBoardRevision(t *testing.T) {
	path := filepath.Join(t.TempDir(), "studio.sqlite3")
	db, e := Open(path)
	if e != nil {
		t.Fatal(e)
	}
	makeMeeting := func(topic string) string {
		t.Helper()
		mid, e := db.Create("例会", "", "", 0, "", topic)
		if e != nil {
			t.Fatal(e)
		}
		return mid
	}
	first := makeMeeting("主题甲")
	if e = db.BindAgent(first, "codex", "thread-A", nil); e != nil {
		t.Fatal(e)
	}
	n, e := db.Tool(first, "create_topic_note", Row{"title": "设计追踪", "kind": "question"}, "AI")
	if e != nil {
		t.Fatal(e)
	}
	note := n.(Row)
	if e = db.Event(first, "speech", 2, 4, "同事甲", "之前的讨论", nil); e != nil {
		t.Fatal(e)
	}
	other := makeMeeting("主题乙")
	var fifth string
	for range 4 {
		fifth = makeMeeting("主题甲")
		_ = makeMeeting("主题乙")
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	db, e = Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if len(db.WorkspaceMeetings(fifth)) != 5 || db.AgentSessions(fifth)["codex"] != "thread-A" {
		t.Fatal("topic binding not preserved")
	}
	if e = db.BindAgent(other, "codex", "thread-A", nil); e == nil || len(db.TopicNotes(other)) > 0 {
		t.Fatal("cross-topic access")
	}
	p, e := db.Tool(fifth, "read_topic_history", Row{"meeting_id": first, "kind": "transcript"}, "AI")
	if e != nil || len(p.(Row)["items"].([]Row)) != 1 {
		t.Fatal(p, e)
	}
	if _, e = db.Tool(other, "read_topic_history", Row{"meeting_id": first, "kind": "transcript"}, "AI"); e == nil {
		t.Fatal("cross-topic history")
	}
	refs := db.PendingTopicFollowups(fifth)
	if len(refs) != 1 {
		t.Fatal("missing opening reminder")
	}
	edited, e := db.Tool(fifth, "set_topic_note_status", Row{"id": note["id"], "version": note["version"], "status": "in_progress"}, "AI")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Tool(first, "set_topic_note_status", Row{"id": note["id"], "version": note["version"], "status": "done"}, "我"); e == nil {
		t.Fatal("stale write")
	}
	if ok, e := db.SaveTopicReminder(fifth, 0, 0, "old", refs); ok || e != nil {
		t.Fatal("stale reminder", e)
	}
	refs = db.PendingTopicFollowups(fifth)
	for i := 0; i < 2; i++ {
		ok, e := db.SaveTopicReminder(fifth, 0, 0, "确认进度", refs)
		if e != nil || ok != (i == 0) {
			t.Fatal("duplicate reminder", e)
		}
	}
	if _, e = db.Tool(fifth, "set_topic_note_status", Row{"id": note["id"], "version": edited.(Row)["version"], "status": "done"}, "AI"); e != nil {
		t.Fatal(e)
	}
	if len(db.PendingTopicFollowups(makeMeeting("主题甲"))) != 0 {
		t.Fatal("completed task still pending")
	}
	if db.TopicNotes(first)[0]["status"] != "done" {
		t.Fatal("shared status lost")
	}
}
func TestQueueCommitIsDurableAndIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "studio.sqlite3")
	db, e := Open(path)
	if e != nil {
		t.Fatal(e)
	}
	mid, e := db.Create("测试", "", "", 4, "", "")
	if e != nil {
		t.Fatal(e)
	}
	j := Job{"path", 4, 6}
	if e = db.Queue(mid, j); e != nil {
		t.Fatal(e)
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	db, e = Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if rs, e := db.PendingJobs(mid); e != nil || len(rs) != 1 || rs[0] != j {
		t.Fatal(rs, e)
	}
	for range 2 {
		if e = db.FinishJob(mid, j.Path, []Segment{{4, 6, "", "test"}}); e != nil {
			t.Fatal(e)
		}
	}
	s, e := db.Snapshot(mid, 10)
	if e != nil {
		t.Fatal(e)
	}
	events := s["events"].([]Row)
	if s["pending"] != 0 || len(events) != 1 || events[0]["speaker"] != "发言人" {
		t.Fatal(s)
	}
	if s["meeting"].(Row)["duration"] != float64(6) {
		t.Fatal("duration")
	}
}
