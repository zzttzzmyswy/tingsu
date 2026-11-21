package store

import (
	"github.com/zzttzzmyswy/tingsu/internal/transcript"
	"testing"
)

func TestImportedTimestampsAndAgentHistory(t *testing.T) {
	db, err := Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	docs := []transcript.Document{{Title: "讨论", Cues: []transcript.Cue{{Speaker: "王", Text: "问题"}, {Speaker: "林", Text: "决定", At: 30.5, End: 32, Timed: true}}}}
	id, err := db.ImportTranscripts(docs)
	if err != nil {
		t.Fatal(err)
	}
	got, err := db.Tool(id, "read_transcript", Row{}, "AI")
	if err != nil {
		t.Fatal(err)
	}
	r := got.([]Row)
	if len(r) != 2 || r[0]["start"] != nil || r[1]["start"] != 30.5 || r[1]["end"] != float64(32) {
		t.Fatal(r)
	}

	if db.Meeting(id)["duration"] != float64(32) {
		t.Fatal(db.Meeting(id))
	}
}
