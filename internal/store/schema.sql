PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
  last_provider TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, demo INTEGER DEFAULT 0,
  source TEXT DEFAULT '', source_label TEXT DEFAULT '', duration REAL DEFAULT 0,
  agent TEXT DEFAULT '', session TEXT DEFAULT '', origin REAL NOT NULL DEFAULT 0, topic_id TEXT REFERENCES topics(id));
CREATE TABLE IF NOT EXISTS meeting_agents (
  meeting TEXT REFERENCES meetings(id) ON DELETE CASCADE, provider TEXT NOT NULL,
  session TEXT NOT NULL, PRIMARY KEY(meeting, provider));
CREATE TABLE IF NOT EXISTS topic_agents (
  topic TEXT REFERENCES topics(id), provider TEXT NOT NULL, session TEXT NOT NULL,
  PRIMARY KEY(topic,provider));
CREATE TABLE IF NOT EXISTS agent_session_scopes (
  provider TEXT NOT NULL, session TEXT NOT NULL, scope TEXT NOT NULL,
  PRIMARY KEY(provider,session));
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY, meeting TEXT REFERENCES meetings(id), kind TEXT NOT NULL,
  at REAL NOT NULL, end REAL NOT NULL, speaker TEXT NOT NULL, text TEXT NOT NULL, files TEXT DEFAULT '[]');
CREATE INDEX IF NOT EXISTS events_meeting ON events(meeting,seq);
CREATE INDEX IF NOT EXISTS events_meeting_time ON events(meeting,at,seq);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, meeting TEXT REFERENCES meetings(id), kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topic_notes (
  id TEXT PRIMARY KEY, topic TEXT NOT NULL REFERENCES topics(id), kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','in_progress','done')),
  source_meeting TEXT NOT NULL REFERENCES meetings(id), source_note TEXT UNIQUE REFERENCES notes(id)
    ON DELETE SET NULL,
  updated_meeting TEXT NOT NULL REFERENCES meetings(id), author TEXT NOT NULL, updated_by TEXT NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS topic_notes_topic ON topic_notes(topic,status);
CREATE TABLE IF NOT EXISTS topic_reminders (meeting TEXT PRIMARY KEY REFERENCES meetings(id));
CREATE TABLE IF NOT EXISTS jobs (
  path TEXT PRIMARY KEY, meeting TEXT REFERENCES meetings(id), start REAL, end REAL);
CREATE TABLE IF NOT EXISTS levels (
  meeting TEXT REFERENCES meetings(id), at INTEGER, peak REAL, PRIMARY KEY(meeting, at));
CREATE TABLE IF NOT EXISTS media (path TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS meetings_topic ON meetings(topic_id);
CREATE TABLE IF NOT EXISTS openai_sessions(account TEXT,id TEXT,title TEXT,PRIMARY KEY(account,id));
CREATE TABLE IF NOT EXISTS openai_pending(account TEXT,id TEXT,outputs TEXT,PRIMARY KEY(account,id));
CREATE INDEX IF NOT EXISTS jobs_meeting_time ON jobs(meeting,start,path);
