package store

import (
	"encoding/json"
	"errors"
)

func agentSessions(q *query, mid string) map[string]string {
	out := map[string]string{}
	rs := q.rows("SELECT a.provider,a.session FROM meetings m JOIN topic_agents a ON a.topic=m.topic_id WHERE m.id=? UNION ALL SELECT a.provider,a.session FROM meetings m JOIN meeting_agents a ON a.meeting=m.id WHERE m.id=? AND m.topic_id IS NULL", mid, mid)
	for _, r := range rs {
		out[str(r["provider"])] = str(r["session"])
	}
	return out
}
func (s *Store) AgentSessions(mid string) map[string]string { return agentSessions(s.read(), mid) }
func scope(m Row) string {
	if topic := str(m["topic_id"]); topic != "" {
		return topic
	}
	return ""
} // Independent sessions may be explicitly reused across independent meetings.
func checkSession(q *query, m Row, provider, sid string) error {
	if m == nil {
		return errors.New("会议不存在")
	}
	if sid == "" {
		return nil
	}
	r := q.one("SELECT scope FROM agent_session_scopes WHERE provider=? AND session=?", provider, sid)
	if r != nil && r["scope"] != scope(m) {
		return errors.New("此 AI 会话已用于其他主题或独立会议，请使用本主题的会话或新建会话")
	}
	return nil
}
func (s *Store) CheckAgentSession(mid, provider, sid string) error {
	q := s.read()
	e := checkSession(q, meeting(q, mid), provider, sid)
	if q.err != nil {
		return q.err
	}
	return e
}
func (s *Store) UnavailableSessionIDs(mid, provider string) map[string]bool {
	q := s.read()
	out := map[string]bool{}
	for _, r := range q.rows("SELECT session FROM agent_session_scopes WHERE provider=? AND scope<>?", provider, scope(meeting(q, mid))) {
		out[str(r["session"])] = true
	}
	return out
}
func (s *Store) BindAgent(mid, provider, sid string, expected *string) error {
	return s.tx(func(q *query) error {
		if provider == "" || sid == "" {
			return errors.New("请选择有效的 AI 会话")
		}
		m := meeting(q, mid)
		if e := checkSession(q, m, provider, sid); e != nil {
			return e
		}
		topic := scope(m)
		if topic != "" && expected != nil {
			current := agentSessions(q, mid)[provider]
			if current != *expected && current != sid {
				return errors.New("本主题的 AI 会话已在其他窗口更改，请重新连接")
			}
		}
		q.exec("INSERT OR IGNORE INTO agent_session_scopes VALUES(?,?,?)", provider, sid, topic)
		if topic != "" {
			q.exec("INSERT INTO topic_agents VALUES(?,?,?) ON CONFLICT(topic,provider) DO UPDATE SET session=excluded.session", topic, provider, sid)
			q.exec("UPDATE topics SET last_provider=? WHERE id=?", provider, topic)
		} else {
			q.exec("INSERT INTO meeting_agents VALUES(?,?,?) ON CONFLICT(meeting,provider) DO UPDATE SET session=excluded.session", mid, provider, sid)
		}
		q.exec("UPDATE meetings SET agent=?,session=? WHERE id=?", provider, sid, mid)
		return nil
	})
}
func (s *Store) OpenAISessions(account string) []Row {
	return s.read().rows("SELECT id,title FROM openai_sessions WHERE account=? ORDER BY rowid DESC", account)
}
func (s *Store) RememberOpenAI(account, id, title string) error {
	_, e := s.db.Exec("INSERT OR REPLACE INTO openai_sessions VALUES(?,?,?)", account, id, title)
	return e
}
func (s *Store) OpenAIPending(account, id string) (map[string]string, error) {
	q := s.read()
	r := q.one("SELECT outputs FROM openai_pending WHERE account=? AND id=?", account, id)
	if r == nil {
		return nil, q.err
	}
	var m map[string]string
	e := json.Unmarshal([]byte(str(r["outputs"])), &m)
	return m, e
}
func (s *Store) SaveOpenAIPending(account, id string, items map[string]string) error {
	if items == nil {
		_, e := s.db.Exec("DELETE FROM openai_pending WHERE account=? AND id=?", account, id)
		return e
	}
	b, e := json.Marshal(items)
	if e != nil {
		return e
	}
	_, e = s.db.Exec("INSERT OR REPLACE INTO openai_pending VALUES(?,?,?)", account, id, string(b))
	return e
}
