// Package store owns the meeting database and the transactional boundary shared by
// capture, conversation tools and the workspace snapshot.
package store

import (
	"crypto/rand"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	_ "modernc.org/sqlite"
)

//go:embed schema.sql
var schema string

//go:embed seed.sql
var seed string

type Row = map[string]any
type Store struct{ db *sql.DB }
type conn interface {
	Query(string, ...any) (*sql.Rows, error)
	Exec(string, ...any) (sql.Result, error)
}
type query struct {
	c   conn
	err error
}

func (q *query) rows(sql string, args ...any) []Row {
	out := []Row{}
	if q.err != nil {
		return out
	}
	rows, err := q.c.Query(sql, args...)
	if err != nil {
		q.err = err
		return out
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		q.err = err
		return out
	}
	for rows.Next() {
		values := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range values {
			ptrs[i] = &values[i]
		}
		if q.err = rows.Scan(ptrs...); q.err != nil {
			return out
		}
		r := Row{}
		for i, k := range cols {
			r[k] = values[i]
		}
		out = append(out, r)
	}
	q.err = rows.Err()
	return out
}
func (q *query) one(sql string, args ...any) Row {
	rows := q.rows(sql, args...)
	if len(rows) == 0 {
		return nil
	}
	return rows[0]
}
func (q *query) exec(sql string, args ...any) int64 {
	if q.err != nil {
		return 0
	}
	res, err := q.c.Exec(sql, args...)
	if err != nil {
		q.err = err
		return 0
	}
	n, err := res.RowsAffected()
	q.err = err
	return n
}
func (s *Store) tx(fn func(*query) error) error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	q := query{c: tx}
	err = fn(&q)
	if q.err != nil {
		return q.err
	}
	if err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) read() *query { return &query{c: s.db} }
func Open(path string) (*Store, error) {
	if path != ":memory:" {
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return nil, err
		}
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	s := &Store{db}
	if _, err = db.Exec("PRAGMA busy_timeout=5000;" + schema); err == nil {
		err = s.Seed()
	}
	if err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}
func (s *Store) Close() error { return s.db.Close() }
func (s *Store) Seed() error {
	return s.tx(func(q *query) error {
		if q.one("SELECT id FROM meetings LIMIT 1") == nil {
			q.exec(seed)
		}
		return nil
	})
}
func id() string  { var b [16]byte; rand.Read(b[:]); return hex.EncodeToString(b[:]) }
func now() string { return time.Now().Format(time.RFC3339) }
func str(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}
func integer(v any, d int) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		if n == math.Trunc(n) {
			return int(n)
		}
	case string:
		x, e := strconv.Atoi(n)
		if e == nil {
			return x
		}
	}
	return d
}
func num(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int64:
		return float64(n)
	case int:
		return float64(n)
	}
	return 0
}
func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
func strRows(rows []Row, key string) []string {
	out := []string{}
	for _, r := range rows {
		out = append(out, str(r[key]))
	}
	return out
}

const meetingsSelect = "SELECT m.*,coalesce(t.name,'') AS topic_name,coalesce(t.last_provider,'') AS topic_provider FROM meetings m LEFT JOIN topics t ON t.id=m.topic_id "

func meeting(q *query, mid string) Row  { return q.one(meetingsSelect+"WHERE m.id=?", mid) }
func (s *Store) Meeting(mid string) Row { return meeting(s.read(), mid) }
func (s *Store) LatestMeetingID() string {
	return str(s.read().one("SELECT id FROM meetings ORDER BY created DESC,rowid DESC LIMIT 1")["id"])
}
func (s *Store) WorkspaceMeetings(mid string) []string {
	return strRows(s.read().rows("SELECT id FROM meetings WHERE id=? OR topic_id=(SELECT topic_id FROM meetings WHERE id=?)", mid, mid), "id")
}
func resolveTopic(q *query, tid, name string) (any, error) {
	name = strings.TrimSpace(name)
	if tid != "" {
		if name != "" || q.one("SELECT id FROM topics WHERE id=?", tid) == nil {
			return nil, errors.New("请选择一个已有主题，或填写新主题名称")
		}
		return tid, nil
	}
	if name == "" {
		return nil, nil
	}
	if utf8.RuneCountInString(name) > 80 {
		return nil, errors.New("主题名称最多 80 个字符")
	}
	key := strings.ToLower(name)
	q.exec("INSERT INTO topics(id,name,name_key) VALUES(?,?,?) ON CONFLICT(name_key) DO NOTHING", id(), name, key)
	return q.one("SELECT id FROM topics WHERE name_key=?", key)["id"], nil
}
func (s *Store) Create(title, source, label string, origin float64, topicID, topicName string) (string, error) {
	mid := id()
	err := s.tx(func(q *query) error {
		topic, e := resolveTopic(q, topicID, topicName)
		if e != nil {
			return e
		}
		q.exec("INSERT INTO meetings(id,title,created,source,source_label,origin,duration,topic_id) VALUES(?,?,?,?,?,?,?,?)", mid, title, now(), source, label, origin, origin, topic)
		return nil
	})
	return mid, err
}
func (s *Store) SetMeetingTopic(mid, tid, name string) error {
	return s.tx(func(q *query) error {
		m := meeting(q, mid)
		if m == nil || integer(m["demo"], 0) != 0 {
			return errors.New("请选择自己的会议")
		}
		topic, e := resolveTopic(q, tid, name)
		if e != nil {
			return e
		}
		if topic == nil {
			return errors.New("请选择要归入的主题")
		}
		if m["topic_id"] == topic {
			return nil
		}
		if m["topic_id"] != nil {
			return errors.New("这场会议已归入其他主题")
		}
		q.exec("UPDATE meetings SET topic_id=?,agent='',session='' WHERE id=?", topic, mid)
		return nil
	})
}
func (s *Store) RenameMeeting(mid, title string) error {
	_, err := s.db.Exec("UPDATE meetings SET title=? WHERE id=?", title, mid)
	return err
}
func (s *Store) SetSource(mid, source, label string, origin, duration float64) error {
	_, err := s.db.Exec("UPDATE meetings SET source=?,source_label=?,origin=?,duration=? WHERE id=?", source, label, origin, duration, mid)
	return err
}
func (s *Store) AddMedia(paths []string) error {
	return s.tx(func(q *query) error {
		for _, p := range paths {
			q.exec("INSERT OR IGNORE INTO media VALUES(?)", p)
		}
		return nil
	})
}
func (s *Store) MediaPaths() []string {
	out := []string{}
	for _, p := range strRows(s.read().rows("SELECT path FROM media UNION SELECT source FROM meetings WHERE source<>''"), "path") {
		if filepath.IsAbs(p) {
			out = append(out, p)
		}
	}
	return out
}

func events(q *query, mid string, limit, before int) []Row {
	if limit <= 0 {
		return q.rows("SELECT * FROM annotated_events WHERE meeting=? ORDER BY at,seq", mid)
	}
	r := q.rows("SELECT * FROM annotated_events WHERE meeting=? AND (?=0 OR seq<?) ORDER BY at DESC,seq DESC LIMIT ?", mid, before, before, limit)
	slices.Reverse(r)
	return r
}
func (s *Store) Events(mid string, limit, before int) []Row {
	return events(s.read(), mid, limit, before)
}
func (s *Store) Event(mid, kind string, at, end float64, speaker, text string, files []string) error {
	names := make([]string, len(files))
	for i, p := range files {
		names[i] = filepath.Base(p)
	}
	j, _ := json.Marshal(names)
	_, e := s.db.Exec("INSERT INTO events(meeting,kind,at,end,speaker,text,files) VALUES(?,?,?,?,?,?,?)", mid, kind, at, end, speaker, text, string(j))
	return e
}
func (s *Store) SearchEvents(mid, needle, speaker, kind string, offset int) (int, []Row, error) {
	w := " FROM events WHERE meeting=?"
	a := []any{mid}
	switch kind {
	case "all", "":
	case "speech":
		w += " AND kind='speech'"
	case "ai":
		w += " AND kind IN ('assistant','user','system')"
	default:
		return 0, nil, errors.New("未知记录类型")
	}
	if needle != "" {
		w += " AND instr(lower(text),lower(?))>0"
		a = append(a, needle)
	}
	if speaker != "" {
		w += " AND speaker=? AND kind='speech'"
		a = append(a, speaker)
	}
	var total int
	var rows []Row
	err := s.tx(func(q *query) error {
		total = integer(q.one("SELECT count(*) AS n"+w, a...)["n"], 0)
		rows = q.rows("SELECT *"+w+" ORDER BY at,seq LIMIT 60 OFFSET ?", append(a, max(0, offset))...)
		return nil
	})
	return total, rows, err
}
func (s *Store) CountEventsFrom(mid string, at float64) int {
	return integer(s.read().one("SELECT count(*) AS n FROM events WHERE meeting=? AND at>=?", mid, at)["n"], 0)
}

type Job struct {
	Path       string
	Start, End float64
}
type Segment struct {
	Start, End    float64
	Speaker, Text string
}

func (s *Store) NextJob(mid string) (*Job, error) {
	q := s.read()
	r := q.one("SELECT path,start,end FROM jobs WHERE meeting=? ORDER BY start,path LIMIT 1", mid)
	if r == nil {
		return nil, q.err
	}
	return &Job{Path: str(r["path"]), Start: num(r["start"]), End: num(r["end"])}, nil
}
func (s *Store) PendingJobs(mid string) ([]Job, error) {
	q := s.read()
	jobs := []Job{}
	for _, r := range q.rows("SELECT path,start,end FROM jobs WHERE meeting=? ORDER BY start,path", mid) {
		jobs = append(jobs, Job{str(r["path"]), num(r["start"]), num(r["end"])})
	}
	return jobs, q.err
}
func (s *Store) PendingCount(mid string) int {
	return integer(s.read().one("SELECT count(*) AS n FROM jobs WHERE meeting=?", mid)["n"], 0)
}
func (s *Store) SpeechSpeakers(mid string) []string {
	return strRows(s.read().rows("SELECT DISTINCT speaker FROM events WHERE meeting=? AND kind='speech'", mid), "speaker")
}
func (s *Store) Queue(mid string, job Job, peaks []float64) error {
	return s.tx(func(q *query) error {
		q.exec("INSERT OR IGNORE INTO jobs(path,meeting,start,end) VALUES(?,?,?,?)", job.Path, mid, job.Start, job.End)
		q.exec("UPDATE meetings SET duration=max(duration,?) WHERE id=?", job.End, mid)
		for i, p := range peaks {
			q.exec("INSERT INTO levels VALUES(?,?,?) ON CONFLICT(meeting,at) DO UPDATE SET peak=max(levels.peak,excluded.peak)", mid, int(job.Start)+i, p)
		}
		return nil
	})
}
func (s *Store) FinishJob(mid, path string, segments []Segment) error {
	return s.tx(func(q *query) error {
		if q.exec("DELETE FROM jobs WHERE path=? AND meeting=?", path, mid) == 0 {
			return nil
		}
		for _, seg := range segments {
			if strings.TrimSpace(seg.Text) == "" {
				continue
			}
			sp := seg.Speaker
			if sp == "" {
				sp = "发言人"
			}
			q.exec("INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES(?,'speech',?,?,?,?)", mid, seg.Start, seg.End, sp, seg.Text)
		}
		return nil
	})
}
func levels(q *query, mid string, start, end float64) []float64 {
	width := max(1., end-start)
	bins := make([]float64, 174)
	if width < 174 {
		rs := q.rows("SELECT at,peak FROM levels WHERE meeting=? AND at>=?", mid, int(start))
		if len(rs) == 0 {
			return []float64{}
		}
		samples := map[int]float64{}
		for _, r := range rs {
			samples[integer(r["at"], 0)] = num(r["peak"])
		}
		for i := range bins {
			bins[i] = samples[int(start+float64(i)*width/174)]
		}
	} else {
		rs := q.rows("SELECT min(173,max(0,cast((at-?)*174.0/? AS integer))) bin,max(peak) peak FROM levels WHERE meeting=? AND at>=? GROUP BY bin", start, width, mid, int(start))
		if len(rs) == 0 {
			return []float64{}
		}
		for _, r := range rs {
			bins[integer(r["bin"], 0)] = num(r["peak"])
		}
	}
	return bins
}
func (s *Store) Snapshot(mid string, limit int) (Row, error) {
	limit = max(1, limit)
	var state Row
	err := s.tx(func(q *query) error {
		m := meeting(q, mid)
		if m == nil {
			return errors.New("会议不存在")
		}
		rs := events(q, mid, limit+1, 0)
		older := len(rs) > limit
		if older {
			rs = rs[1:]
		}
		state = Row{"meetings": q.rows(meetingsSelect + "ORDER BY m.demo ASC,m.created DESC,m.rowid DESC"), "topics": q.rows("SELECT t.id,t.name,count(m.id) AS meeting_count FROM topics t LEFT JOIN meetings m ON m.topic_id=t.id GROUP BY t.id ORDER BY max(m.rowid) DESC,t.name"), "meeting": m, "events": rs, "older": older, "trackSpeech": q.rows("SELECT at,end,speaker FROM events WHERE meeting=? AND kind='speech' ORDER BY at,seq", mid), "speakers": q.rows("SELECT speaker,sum(max(0,end-at)) seconds,min(seq) first FROM events WHERE meeting=? AND kind='speech' GROUP BY speaker ORDER BY first", mid), "levels": levels(q, mid, num(m["origin"]), num(m["duration"])), "notes": notes(q, mid), "topicNotes": topicNotes(q, mid), "pending": integer(q.one("SELECT count(*) AS n FROM jobs WHERE meeting=?", mid)["n"], 0), "meetingSessions": agentSessions(q, mid)}
		return nil
	})
	return state, err
}
