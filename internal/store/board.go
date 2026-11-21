package store

import (
	"errors"
	"slices"
	"strings"
)

const boardFrom = " FROM topic_notes n JOIN meetings current ON current.topic_id=n.topic JOIN meetings source ON source.id=n.source_meeting JOIN meetings edited ON edited.id=n.updated_meeting WHERE current.id=?"
const boardFields = "n.*,source.title AS source_title,edited.title AS updated_title"
const boardPreview = "n.id,n.kind,n.title,substr(n.body,1,240) AS body,n.status,n.version,n.source_meeting,source.title AS source_title"
const boardOrder = "(n.status='done'),n.rowid DESC"
const priorMeetings = " FROM meetings m JOIN meetings current ON current.topic_id=m.topic_id WHERE current.id=? AND m.rowid<current.rowid"

func notes(q *query, mid string) []Row {
	return q.rows("SELECT * FROM notes WHERE meeting=? ORDER BY created DESC,rowid DESC", mid)
}
func topicNotes(q *query, mid string) []Row {
	return q.rows("SELECT "+boardFields+boardFrom+" ORDER BY "+boardOrder, mid)
}
func (s *Store) Notes(mid string) []Row      { return notes(s.read(), mid) }
func (s *Store) TopicNotes(mid string) []Row { return topicNotes(s.read(), mid) }
func followups(q *query, mid string) []Row {
	return q.rows("SELECT "+boardPreview+boardFrom+" AND n.kind IN ('question','task') AND n.status<>'done' AND source.rowid<current.rowid AND NOT EXISTS(SELECT 1 FROM topic_reminders r WHERE r.meeting=current.id) ORDER BY n.rowid LIMIT 12", mid)
}
func (s *Store) PendingTopicFollowups(mid string) []Row { return followups(s.read(), mid) }
func (s *Store) SaveTopicReminder(mid string, at, end float64, text string, expected []Row) (bool, error) {
	if strings.TrimSpace(text) == "" {
		return false, nil
	}
	saved := false
	err := s.tx(func(q *query) error {
		current := followups(q, mid)
		if len(current) == 0 || len(current) != len(expected) {
			return nil
		}
		for i, r := range current {
			if r["id"] != expected[i]["id"] || integer(r["version"], 0) != integer(expected[i]["version"], -1) {
				return nil
			}
		}
		q.exec("INSERT INTO topic_reminders VALUES(?)", mid)
		q.exec("INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES(?,'assistant',?,?,'听序 AI',?)", mid, at, end, text)
		saved = true
		return nil
	})
	return saved && err == nil, err
}
func requireTopic(q *query, mid string) (string, error) {
	t := str(q.one("SELECT topic_id FROM meetings WHERE id=?", mid)["topic_id"])
	if t == "" {
		return "", errors.New("请先将本场会议归入主题")
	}
	return t, nil
}
func boardNote(q *query, mid, nid string) Row {
	return q.one("SELECT "+boardFields+boardFrom+" AND n.id=?", mid, nid)
}
func insertBoard(q *query, mid, topic, kind, title, body, status, author string, src any) Row {
	nid, ts := id(), now()
	q.exec("INSERT INTO topic_notes(id,topic,kind,title,body,status,source_meeting,source_note,updated_meeting,author,updated_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", nid, topic, kind, title, body, status, mid, src, mid, author, author, ts, ts)
	return boardNote(q, mid, nid)
}
func share(q *query, mid, nid, author string) (Row, error) {
	t, err := requireTopic(q, mid)
	if err != nil {
		return nil, err
	}
	local := q.one("SELECT kind,title,body FROM notes WHERE meeting=? AND id=?", mid, nid)
	if local == nil {
		return nil, errors.New("本场会议中找不到这条便签")
	}
	existing := q.one("SELECT id FROM topic_notes WHERE topic=? AND source_note=?", t, nid)
	if existing != nil {
		return boardNote(q, mid, str(existing["id"])), nil
	}
	return insertBoard(q, mid, t, str(local["kind"]), str(local["title"]), str(local["body"]), "open", author, nid), nil
}
func (s *Store) ShareNote(mid, nid string) (Row, error) {
	var r Row
	err := s.tx(func(q *query) error { var err error; r, err = share(q, mid, nid, "我"); return err })
	return r, err
}
func page(q *query, fields, where, order string, args []any, offset, limit int) Row {
	total := q.one("SELECT count(*) AS n "+where, args...)["n"]
	return Row{"total": total, "offset": offset, "items": q.rows("SELECT "+fields+" "+where+" ORDER BY "+order+" LIMIT ? OFFSET ?", append(args, limit, offset)...)}
}
func validateNote(args Row) (string, string, string, error) {
	kind := str(args["kind"])
	if _, ok := args["kind"]; !ok {
		kind = "idea"
	}
	title := truncate(strings.TrimSpace(str(args["title"])), 160)
	body := truncate(strings.TrimSpace(str(args["body"])), 16000)
	if title == "" || !slices.Contains([]string{"idea", "question", "task", "decision"}, kind) {
		return "", "", "", errors.New("请填写便签标题并选择类型")
	}
	return kind, title, body, nil
}
func boardWrite(q *query, mid, name string, args Row, author string) (Row, error) {
	t, e := requireTopic(q, mid)
	if e != nil {
		return nil, e
	}
	status := str(args["status"])
	if name == "create_topic_note" && status == "" {
		status = "open"
	}
	if name != "delete_topic_note" && !slices.Contains([]string{"open", "in_progress", "done"}, status) {
		return nil, errors.New("未知的主题便签状态")
	}
	if name == "create_topic_note" {
		k, h, b, e := validateNote(args)
		if e != nil {
			return nil, e
		}
		return insertBoard(q, mid, t, k, h, b, status, author, nil), nil
	}
	nid := str(args["id"])
	r := boardNote(q, mid, nid)
	if r == nil {
		return nil, errors.New("当前主题找不到这条便签")
	}
	if integer(args["version"], -1) != integer(r["version"], 0) {
		return nil, errors.New("主题便签已更新，请重新读取后再修改")
	}
	if name == "delete_topic_note" {
		q.exec("DELETE FROM topic_notes WHERE id=? AND topic=?", nid, t)
		return Row{"deleted": true}, nil
	}
	k, h, b := str(r["kind"]), str(r["title"]), str(r["body"])
	if name == "update_topic_note" {
		k, h, b, e = validateNote(args)
		if e != nil {
			return nil, e
		}
	}
	if k == r["kind"] && h == r["title"] && b == r["body"] && status == r["status"] {
		return r, nil
	}
	q.exec("UPDATE topic_notes SET kind=?,title=?,body=?,status=?,updated_meeting=?,updated_by=?,updated=?,version=version+1 WHERE id=? AND topic=?", k, h, b, status, mid, author, now(), nid, t)
	return boardNote(q, mid, nid), nil
}
func applyTool(q *query, mid, name string, args Row, author string) (any, error) {
	needle := truncate(str(args["query"]), 500)
	offset := max(0, integer(args["offset"], 0))
	limit := func(d, m int) int { return min(m, max(1, integer(args["limit"], d))) }
	nid := str(args["id"])
	switch name {
	case "list_topic_notes":
		if _, e := requireTopic(q, mid); e != nil {
			return nil, e
		}
		w, vals := boardFrom, []any{mid}
		st := str(args["status"])
		if st != "" && st != "all" {
			if !slices.Contains([]string{"open", "in_progress", "done"}, st) {
				return nil, errors.New("未知的主题便签状态")
			}
			w += " AND n.status=?"
			vals = append(vals, st)
		}
		if needle != "" {
			w += " AND instr(lower(n.title||' '||n.body),lower(?))>0"
			vals = append(vals, needle)
		}
		return page(q, boardFields, w, boardOrder, vals, offset, limit(20, 50)), nil
	case "share_note_to_topic":
		return share(q, mid, nid, author)
	case "create_topic_note", "update_topic_note", "set_topic_note_status", "delete_topic_note":
		return boardWrite(q, mid, name, args, author)
	case "list_topic_meetings":
		w, a := priorMeetings, []any{mid}
		if needle != "" {
			w += " AND (instr(lower(m.title),lower(?))>0 OR EXISTS (SELECT 1 FROM events e WHERE e.meeting=m.id AND e.kind='speech' AND instr(lower(e.text),lower(?))>0) OR EXISTS (SELECT 1 FROM notes n WHERE n.meeting=m.id AND instr(lower(n.title||' '||n.body),lower(?))>0))"
			a = append(a, needle, needle, needle)
		}
		return page(q, "m.id,m.title,m.created", w, "m.rowid DESC", a, offset, limit(10, 50)), nil
	case "read_topic_history":
		target := str(args["meeting_id"])
		m := q.one("SELECT m.id,m.title,m.created "+priorMeetings+" AND m.id=?", mid, target)
		if m == nil {
			return nil, errors.New("只能读取当前主题中的往期会议")
		}
		var fields, w, order string
		switch str(args["kind"]) {
		case "notes":
			fields = "kind,title,body,author,created"
			w = " FROM notes WHERE meeting=? AND instr(lower(title||' '||body),lower(?))>0"
			order = "created,rowid"
		case "transcript", "chat":
			k := "kind='speech'"
			if args["kind"] == "chat" {
				k = "kind IN ('assistant','user')"
			}
			fields = "kind,at AS start,end,speaker,text"
			w = " FROM events WHERE meeting=? AND " + k + " AND instr(lower(text||' '||speaker),lower(?))>0"
			order = "at,seq"
		default:
			return nil, errors.New("请选择转写、便签或 AI 对话记录")
		}
		p := page(q, fields, w, order, []any{target, needle}, offset, limit(10, 50))
		p["meeting"] = m
		return p, nil
	case "read_transcript":
		return q.rows("SELECT at AS start,end,speaker,text FROM events WHERE meeting=? AND kind='speech' AND instr(lower(text||speaker),lower(?))>0 ORDER BY at,seq LIMIT ? OFFSET ?", mid, needle, limit(30, 100), offset), nil
	case "list_notes":
		return notes(q, mid), nil
	case "delete_note":
		if q.exec("DELETE FROM notes WHERE id=? AND meeting=?", nid, mid) == 0 {
			return nil, errors.New("便签不存在")
		}
		return Row{"deleted": true}, nil
	case "create_note", "update_note":
		k, h, b, e := validateNote(args)
		if e != nil {
			return nil, e
		}
		if name == "create_note" {
			nid = id()
			q.exec("INSERT INTO notes VALUES(?,?,?,?,?,?,?)", nid, mid, k, h, b, author, now())
		} else if q.exec("UPDATE notes SET kind=?,title=?,body=? WHERE id=? AND meeting=?", k, h, b, nid, mid) == 0 {
			return nil, errors.New("便签不存在")
		}
		return Row{"id": nid, "title": h}, nil
	default:
		return nil, errors.New("无法识别的会议操作")
	}
}
func (s *Store) Tool(mid, name string, args Row, author string) (any, error) {
	var v any
	err := s.tx(func(q *query) error { var e error; v, e = applyTool(q, mid, name, args, author); return e })
	return v, err
}
func (s *Store) AgentContext(mid string) Row {
	var ctx Row
	_ = s.tx(func(q *query) error {
		m := meeting(q, mid)
		recent := q.rows("SELECT at,speaker,text FROM events WHERE meeting=? AND kind='speech' ORDER BY at DESC,seq DESC LIMIT 32", mid)
		slices.Reverse(recent)
		ctx = Row{"会议": m["title"], "示例": integer(m["demo"], 0) != 0, "近期发言": recent, "当前便签": q.rows("SELECT id,kind,title,body FROM notes WHERE meeting=? ORDER BY created DESC,rowid DESC LIMIT 40", mid)}
		if m["topic_id"] != nil {
			history := q.rows("SELECT m.id,m.title,m.created "+priorMeetings+" ORDER BY m.rowid DESC LIMIT 5", mid)
			for _, p := range history {
				rs := q.rows("SELECT at,speaker,substr(text,1,200) AS text FROM events WHERE meeting=? AND kind='speech' ORDER BY at DESC,seq DESC LIMIT 2", p["id"])
				slices.Reverse(rs)
				p["发言摘录"] = rs
				p["便签摘录"] = q.rows("SELECT kind,substr(title,1,100) AS title,substr(body,1,180) AS body FROM notes WHERE meeting=? ORDER BY created DESC,rowid DESC LIMIT 2", p["id"])
			}
			ctx["主题"] = m["topic_name"]
			ctx["本场会议ID"] = mid
			ctx["往次会议"] = history
			ctx["主题共享便签"] = q.rows("SELECT "+boardPreview+boardFrom+" ORDER BY "+boardOrder+" LIMIT 20", mid)
		}
		return nil
	})
	return ctx
}
