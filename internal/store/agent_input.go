package store

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"slices"
)

// AgentBatch is a snapshot. A successful provider turn acknowledges exactly this snapshot.
type AgentBatch struct {
	Context     Row
	More        bool
	HasSpeech   bool
	Acknowledge func() error
}

type delivery struct {
	Seq    int
	Offset int // rune offset within a single long imported utterance
	Seen   map[string]string
}

func (d *delivery) changedRows(ctx Row, group string, rows []Row, previous map[string]string) {
	changed := []Row{}
	for _, r := range rows {
		id := str(r["id"])
		data, _ := json.Marshal(r)
		sum := sha256.Sum256(data)
		value := hex.EncodeToString(sum[:])
		key := group + "/" + id
		d.Seen[key] = value
		if previous[key] != value {
			changed = append(changed, r)
		}
	}
	ctx[group] = changed
}

func (s *Store) AgentInput(mid, provider, sid string) (AgentBatch, error) {
	var batch AgentBatch
	var revision int
	var next delivery
	err := s.tx(func(q *query) error {
		m := meeting(q, mid)
		if m == nil {
			return errors.New("会议不存在")
		}
		stored := q.one("SELECT state,revision FROM agent_delivery WHERE meeting=? AND provider=? AND session=?", mid, provider, sid)
		if stored != nil {
			if err := json.Unmarshal([]byte(str(stored["state"])), &next); err != nil {
				return err
			}
			revision = integer(stored["revision"], 0)
		}
		previous := next.Seen
		next.Seen = map[string]string{}
		ctx := Row{"会议": m["title"], "本场会议ID": mid, "示例": integer(m["demo"], 0) != 0}
		next.changedRows(ctx, "当前便签", q.rows("SELECT id,kind,title,body FROM notes WHERE meeting=? ORDER BY id", mid), previous)
		if m["topic_id"] != nil {
			ctx["主题"] = m["topic_name"]
			next.changedRows(ctx, "往次会议", q.rows("SELECT m.id,m.title,m.created "+priorMeetings+" ORDER BY m.rowid DESC", mid), previous)
			next.changedRows(ctx, "主题共享便签", q.rows("SELECT "+boardFields+boardFrom+" ORDER BY "+boardOrder, mid), previous)
		}
		var removed []string
		for k := range previous {
			if _, ok := next.Seen[k]; !ok {
				removed = append(removed, k)
			}
		}
		if len(removed) > 0 {
			slices.Sort(removed)
			ctx["已移除"] = removed
		}
		const maxChars = 16000
		const maxRows = 96
		// Read in insertion order, including late ASR results with earlier timestamps.
		rows := q.rows("SELECT seq, CASE WHEN timed THEN at END AS at, CASE WHEN timed THEN end END AS end,speaker,substr(text,CASE WHEN seq=? THEN ?+1 ELSE 1 END,?) AS text,length(text) AS chars FROM annotated_events WHERE meeting=? AND kind='speech' AND (seq>? OR (seq=? AND ?>0)) ORDER BY seq LIMIT ?", next.Seq, next.Offset, maxChars, mid, next.Seq, next.Seq, next.Offset, maxRows+1)
		remaining := maxChars
		spoken := []Row{}
		for _, r := range rows {
			if remaining == 0 || len(spoken) == maxRows {
				batch.More = true
				break
			}
			seq := integer(r["seq"], 0)
			start := 0
			if seq == next.Seq {
				start = next.Offset
			}
			chars := integer(r["chars"], 0)
			delete(r, "chars")
			part := []rune(str(r["text"]))
			if len(part) > remaining {
				part = part[:remaining]
			}
			r["text"] = string(part)
			if start > 0 || start+len(part) < chars {
				r["字符偏移"] = start
			}
			spoken = append(spoken, r)
			remaining -= len(part)
			next.Seq, next.Offset = seq, 0
			if start+len(part) < chars {
				next.Offset = start + len(part)
				batch.More = true
				break
			}
		}
		ctx["新增发言"] = spoken
		batch.Context = ctx
		batch.HasSpeech = len(spoken) > 0
		return nil
	})
	if err != nil {
		return AgentBatch{}, err
	}
	state, err := json.Marshal(next)
	if err != nil {
		return AgentBatch{}, err
	}
	batch.Acknowledge = func() error {
		return s.tx(func(q *query) error {
			var n int64
			if revision == 0 {
				n = q.exec("INSERT INTO agent_delivery(meeting,provider,session,state,revision) VALUES(?,?,?,?,1) ON CONFLICT DO NOTHING", mid, provider, sid, string(state))
			} else {
				n = q.exec("UPDATE agent_delivery SET state=?,revision=revision+1 WHERE meeting=? AND provider=? AND session=? AND revision=?", string(state), mid, provider, sid, revision)
			}
			if n != 1 {
				return errors.New("会话进度已变更，请重试")
			}
			return nil
		})
	}
	return batch, nil
}
