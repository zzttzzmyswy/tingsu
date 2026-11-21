package store

import "github.com/zzttzzmyswy/tingsu/internal/transcript"

// ImportTranscripts creates one meeting per document. A failed batch leaves no partial meetings.
func (s *Store) ImportTranscripts(docs []transcript.Document) (string, error) {
	first := ""
	err := s.tx(func(q *query) error {
		for _, d := range docs {
			mid := id()
			if first == "" {
				first = mid
			}
			var end float64
			for _, c := range d.Cues {
				end = max(end, c.End)
			}
			q.exec("INSERT INTO meetings(id,title,created,duration,source_label) VALUES(?,?,?,?,?)", mid, d.Title, now(), end, "逐字稿")
			for _, c := range d.Cues {
				q.exec("INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES(?,'speech',?,?,?,?)", mid, c.At, c.End, c.Speaker, c.Text)
				if !c.Timed {
					q.exec("INSERT INTO untimed_transcript_events VALUES(last_insert_rowid())")
				}
			}
		}
		return nil
	})
	return first, err
}
