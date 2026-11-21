from __future__ import annotations

import json
import os
import sqlite3
import threading
import unicodedata
import uuid
import wave
from array import array
from contextlib import closing
from sys import byteorder
from datetime import datetime
from pathlib import Path

from .runtime import app_directory

DATA = app_directory("data")
CONFIG = app_directory("config")
CACHE = app_directory("cache")
KINDS = {"question": "待解问题", "task": "待办事项", "idea": "灵感想法", "decision": "会议决策"}
TOPIC_STATUSES = ("open", "in_progress", "done")


def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Settings:
    defaults = dict(
        provider="siliconflow",
        siliconflow_model="XingChenAGI/XingChenASR-Diarize-V3.0",
        sophnet_model="Fun-ASR",
        agent="codex",
        local_agent_options={},
        model="",
        codex_path="",
        claude_path="",
        deepseek_path="",
        auto=True,
        share_frames=False,
        chunk=12,
        selected="",
    )

    def __init__(self):
        self.path = private_dir(CONFIG) / "settings.json"
        self.values = dict(self.defaults)
        if self.path.exists():
            self.values.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, **fields):
        self.values.update(fields)
        tmp = self.path.with_suffix(".tmp-" + uid())
        with os.fdopen(os.open(tmp, os.O_EXCL | os.O_CREAT | os.O_WRONLY, 0o600), "w", encoding="utf-8") as f:
            json.dump(self.values, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def key(self, provider):
        return os.getenv(provider.upper() + "_API_KEY") or self.values.get(provider + "_key", "")

    def public(self):
        return {
            **{k: self.values[k] for k in self.defaults},
            "hasKeys": {k: bool(self.key(k)) for k in ("siliconflow", "sophnet", "openai")},
        }


class OpenAISessionStore:
    """Provider-account session index and tool receipts, including pre-existing profiles."""

    def __init__(self, account):
        self.account = account
        self.path = private_dir(app_directory("data")) / "openai-sessions.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (account TEXT, id TEXT, title TEXT, PRIMARY KEY(account,id))")
            db.execute("CREATE TABLE IF NOT EXISTS pending (account TEXT, id TEXT, outputs TEXT, PRIMARY KEY(account,id))")

    def remember(self, session_id, title):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (self.account, session_id, title))

    def sessions(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,title FROM sessions WHERE account=? ORDER BY rowid DESC", (self.account,))
            return [{"id": sid, "title": title} for sid, title in rows]

    def pending(self, session_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT outputs FROM pending WHERE account=? AND id=?", (self.account, session_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save_pending(self, session_id, outputs):
        key = (self.account, session_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            if outputs is None:
                db.execute("DELETE FROM pending WHERE account=? AND id=?", key)
            else:
                db.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?)", (*key, json.dumps(outputs, ensure_ascii=False)))


class Store:
    def __init__(self):
        private_dir(DATA)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(DATA / "studio.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            last_provider TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, demo INTEGER DEFAULT 0,
            source TEXT DEFAULT '', source_label TEXT DEFAULT '', duration REAL DEFAULT 0,
            agent TEXT DEFAULT '', session TEXT DEFAULT '');
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
        """)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {r["name"] for r in self.rows("PRAGMA table_info(meetings)")}
            if "origin" not in columns:
                self.db.execute("ALTER TABLE meetings ADD COLUMN origin REAL NOT NULL DEFAULT 0")
            if "topic_id" not in columns:
                self.db.execute("ALTER TABLE meetings ADD COLUMN topic_id TEXT REFERENCES topics(id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS meetings_topic ON meetings(topic_id)")
            self.db.execute("INSERT OR IGNORE INTO meeting_agents(meeting,provider,session) "
                            "SELECT id,agent,session FROM meetings "
                            "WHERE topic_id IS NULL AND agent<>'' AND session<>''")
            self.db.execute("INSERT OR IGNORE INTO agent_session_scopes(provider,session,scope) "
                            "SELECT provider,session,'' FROM meeting_agents")
        if not self.one("SELECT id FROM meetings LIMIT 1"):
            self.seed()

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        return next(iter(self.rows(sql, args)), None)

    def write(self, sql, args=()):
        with self.lock, self.db:
            return self.db.execute(sql, args).rowcount

    def _resolve_topic(self, topic_id, topic_name):
        """Resolve or create a topic inside the caller's write transaction."""
        name = unicodedata.normalize("NFC", topic_name or "").strip()
        if topic_id:
            if name or not self.one("SELECT id FROM topics WHERE id=?", (topic_id,)):
                raise ValueError("请选择一个已有主题，或填写新主题名称")
            return topic_id
        if not name:
            return None
        if len(name) > 80:
            raise ValueError("主题名称最多 80 个字符")
        key = name.casefold()
        self.db.execute("INSERT INTO topics(id,name,name_key) VALUES(?,?,?) "
                        "ON CONFLICT(name_key) DO NOTHING", (uid(), name, key))
        return self.one("SELECT id FROM topics WHERE name_key=?", (key,))["id"]

    def create(self, title, demo=False, *, source="", source_label="", origin=0, topic_id="", topic_name=""):
        mid = uid()
        with self.lock, self.db:
            topic = self._resolve_topic(topic_id, topic_name)
            self.db.execute(
                "INSERT INTO meetings(id,title,created,demo,source,source_label,origin,duration,topic_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (mid, title, stamp(), demo, source, source_label, origin, origin, topic)
            )
        return mid

    def set_meeting_topic(self, mid, topic_id="", topic_name=""):
        """Archive an independent meeting into a topic; its old remote chats keep their scope."""
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            meeting = self.meeting(mid)
            if not meeting or meeting["demo"]:
                raise ValueError("请选择自己的会议")
            topic = self._resolve_topic(topic_id, topic_name)
            if not topic:
                raise ValueError("请选择要归入的主题")
            if meeting["topic_id"] == topic:
                return
            if meeting["topic_id"]:
                raise ValueError("这场会议已归入其他主题")
            self.db.execute("UPDATE meetings SET topic_id=?,agent='',session='' WHERE id=?", (topic, mid))

    _MEETINGS = ("SELECT m.*,coalesce(t.name,'') AS topic_name,coalesce(t.last_provider,'') AS topic_provider "
                 "FROM meetings m LEFT JOIN topics t ON t.id=m.topic_id ")

    def meeting(self, mid):
        return self.one(self._MEETINGS + "WHERE m.id=?", (mid,))

    def workspace_meetings(self, mid):
        return [row["id"] for row in self.rows(
            "SELECT id FROM meetings WHERE id=? OR topic_id=(SELECT nullif(topic_id,'') FROM meetings WHERE id=?)",
            (mid, mid))]

    def topics(self):
        return self.rows("SELECT t.id,t.name,count(m.id) AS meeting_count FROM topics t "
                         "LEFT JOIN meetings m ON m.topic_id=t.id GROUP BY t.id "
                         "ORDER BY max(m.rowid) DESC,t.name")

    def latest_meeting_id(self):
        return self.one("SELECT id FROM meetings ORDER BY created DESC,rowid DESC LIMIT 1")["id"]

    def rename_meeting(self, mid, name):
        self.write("UPDATE meetings SET title=? WHERE id=?", (name, mid))

    def set_source(self, mid, source, label, origin, duration):
        self.write("UPDATE meetings SET source=?,source_label=?,origin=?,duration=? WHERE id=?",
                   (source, label, origin, duration, mid))

    def add_media(self, paths):
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO media(path) VALUES(?)", ((p,) for p in paths))

    def media_paths(self):
        return [Path(row["path"]) for row in self.rows(
            "SELECT path FROM media UNION SELECT source FROM meetings WHERE source<>''"
        ) if Path(row["path"]).is_absolute()]

    def pending_jobs(self, mid):
        return self.rows("SELECT path,start,end FROM jobs WHERE meeting=? ORDER BY start,path", (mid,))

    def pending_count(self, mid):
        return self.one("SELECT count(*) AS n FROM jobs WHERE meeting=?", (mid,))["n"]

    def speech_speakers(self, mid):
        return [r["speaker"] for r in self.rows(
            "SELECT DISTINCT speaker FROM events WHERE meeting=? AND kind='speech'", (mid,)
        )]

    def count_events_from(self, mid, at):
        return self.one("SELECT count(*) AS n FROM events WHERE meeting=? AND at>=?", (mid, at))["n"]

    def search_events(self, mid, query, speaker, kind_filter, offset):
        kinds = {"all": "", "speech": " AND kind='speech'", "ai": " AND kind IN ('assistant','user','system')"}
        if kind_filter not in kinds:
            raise ValueError("未知记录类型")
        where = " FROM events WHERE meeting=?" + kinds[kind_filter]
        args = [mid]
        if query:
            where += " AND instr(lower(text),lower(?))>0"
            args.append(query)
        if speaker:
            where += " AND speaker=? AND kind='speech'"
            args.append(speaker)
        with self.lock:
            count = self.one("SELECT count(*) AS n" + where, args)["n"]
            rows = self.rows("SELECT *" + where + " ORDER BY at,seq LIMIT 60 OFFSET ?", (*args, offset))
        return count, rows

    def workspace_snapshot(self, mid, limit):
        # A committed ASR chunk should appear in the timeline, speaker totals and job count together.
        with self.lock:
            meeting = self.meeting(mid)
            events = self.events(mid, limit + 1)
            return dict(
                meetings=self.rows(self._MEETINGS + "ORDER BY m.demo ASC,m.created DESC,m.rowid DESC"),
                topics=self.topics(),
                meeting=meeting, events=events[-limit:], older=len(events) > limit,
                trackSpeech=self.rows("SELECT at,end,speaker FROM events WHERE meeting=? AND kind='speech' "
                                      "ORDER BY at,seq", (mid,)),
                speakers=self.rows("SELECT speaker,sum(max(0,end-at)) seconds,min(seq) first FROM events "
                                   "WHERE meeting=? AND kind='speech' GROUP BY speaker ORDER BY first", (mid,)),
                levels=self.levels(mid, meeting["origin"], meeting["duration"]), notes=self.notes(mid),
                topicNotes=self.topic_notes(mid),
                pending=self.pending_count(mid), meetingSessions=self.agent_sessions(mid),
            )

    def agent_context(self, mid):
        with self.lock:
            meeting = self.meeting(mid)
            recent = self.rows("SELECT at,speaker,text FROM events WHERE meeting=? AND kind='speech' "
                               "ORDER BY at DESC,seq DESC LIMIT 32", (mid,))
            notes = self.rows("SELECT id,kind,title,body FROM notes WHERE meeting=? "
                              "ORDER BY created DESC,rowid DESC LIMIT 40", (mid,))
            context = {"会议": meeting["title"], "示例": bool(meeting["demo"]),
                       "近期发言": recent[::-1], "当前便签": notes}
            if meeting["topic_id"]:
                previous = self.rows("SELECT m.id,m.title,m.created " + self._PRIOR_MEETINGS +
                                     " ORDER BY m.rowid DESC LIMIT 5", (mid,))
                for entry in previous:
                    entry["发言摘录"] = self.rows(
                        "SELECT at,speaker,substr(text,1,200) AS text FROM events "
                        "WHERE meeting=? AND kind='speech' ORDER BY at DESC,seq DESC LIMIT 2", (entry["id"],)
                    )[::-1]
                    entry["便签摘录"] = self.rows(
                        "SELECT kind,substr(title,1,100) AS title,substr(body,1,180) AS body FROM notes "
                        "WHERE meeting=? ORDER BY created DESC,rowid DESC LIMIT 2", (entry["id"],)
                    )
                context.update({"主题": meeting["topic_name"], "本场会议ID": mid, "往次会议": previous})
                context["主题共享便签"] = self.rows("SELECT " + self._BOARD_PREVIEW + self._BOARD_FROM +
                                                  " ORDER BY " + self._BOARD_ORDER + " LIMIT 20", (mid,))
            return context

    def check_agent_session(self, mid, provider, session):
        if not session:
            return
        row = self.one("SELECT scope FROM agent_session_scopes WHERE provider=? AND session=?", (provider, session))
        if row and row["scope"] != (self.meeting(mid)["topic_id"] or ""):
            raise ValueError("此 AI 会话已用于其他主题或独立会议，请使用本主题的会话或新建会话")

    def unavailable_session_ids(self, mid, provider):
        rows = self.rows("SELECT session FROM agent_session_scopes WHERE provider=? AND scope<>?",
                         (provider, self.meeting(mid)["topic_id"] or ""))
        return {row["session"] for row in rows}

    def bind_agent(self, mid, provider, session, *, expected_session=None):
        if not provider or not session:
            raise ValueError("请选择有效的 AI 会话")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            meeting = self.meeting(mid)
            if not meeting:
                raise ValueError("会议不存在")
            topic = meeting["topic_id"]
            self.check_agent_session(mid, provider, session)
            if topic and expected_session is not None:
                current = self.agent_sessions(mid).get(provider, "")
                if current != expected_session and current != session:
                    raise ValueError("本主题的 AI 会话已在其他窗口更改，请重新连接")
            self.db.execute("INSERT OR IGNORE INTO agent_session_scopes(provider,session,scope) VALUES(?,?,?)",
                            (provider, session, topic or ""))
            if topic:
                self.db.execute(
                    "INSERT INTO topic_agents VALUES(?,?,?) ON CONFLICT(topic,provider) "
                    "DO UPDATE SET session=excluded.session", (topic, provider, session)
                )
                self.db.execute("UPDATE topics SET last_provider=? WHERE id=?", (provider, topic))
            else:
                self.db.execute(
                    "INSERT INTO meeting_agents VALUES(?,?,?) ON CONFLICT(meeting,provider) "
                    "DO UPDATE SET session=excluded.session", (mid, provider, session)
                )
            self.db.execute("UPDATE meetings SET agent=?,session=? WHERE id=?", (provider, session, mid))

    def agent_sessions(self, mid):
        # Choose bindings by scope even for an old meeting subsequently filed into a topic.
        rows = self.rows("SELECT a.provider,a.session FROM meetings m "
                         "JOIN topic_agents a ON a.topic=m.topic_id WHERE m.id=? "
                         "UNION ALL SELECT a.provider,a.session FROM meetings m "
                         "JOIN meeting_agents a ON a.meeting=m.id WHERE m.id=? AND m.topic_id IS NULL", (mid, mid))
        return {r["provider"]: r["session"] for r in rows}

    def events(self, mid, limit=0, before=0):
        if limit:
            return list(
                reversed(
                    self.rows(
                        "SELECT * FROM events WHERE meeting=? AND (?=0 OR seq<?) ORDER BY at DESC,seq DESC LIMIT ?",
                        (mid, before, before, limit),
                    )
                )
            )
        return self.rows("SELECT * FROM events WHERE meeting=? ORDER BY at,seq", (mid,))

    def event(self, mid, kind, at, end, speaker, text, files=()):
        self.write(
            "INSERT INTO events(meeting,kind,at,end,speaker,text,files) VALUES(?,?,?,?,?,?,?)",
            (
                mid,
                kind,
                at,
                end,
                speaker,
                text,
                json.dumps([Path(p).name for p in files], ensure_ascii=False),
            ),
        )

    def queue(self, mid, path, start, end):
        with wave.open(str(path)) as wav:
            rate = wav.getframerate()
            envelope = []
            while block := wav.readframes(rate):
                samples = array("h", block)
                if byteorder != "little":
                    samples.byteswap()
                peak = max(abs(min(samples)), max(samples)) / 32768
                envelope.append((mid, int(start) + len(envelope), peak))
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?)", (str(path), mid, start, end))
            self.db.execute("UPDATE meetings SET duration=max(duration,?) WHERE id=?", (end, mid))
            self.db.executemany(
                """INSERT INTO levels VALUES(?,?,?) ON CONFLICT(meeting,at)
                                DO UPDATE SET peak=max(levels.peak,excluded.peak)""",
                envelope,
            )

    def levels(self, mid, origin, end):
        width = max(1.0, end - origin)
        if width < 174:
            recent = self.rows(
                "SELECT at,peak FROM levels WHERE meeting=? AND at>=?", (mid, int(origin))
            )
            samples = {p["at"]: p["peak"] for p in recent}
            return (
                [samples.get(int(origin + i * width / 174), 0) for i in range(174)] if samples else []
            )
        points = self.rows(
            """SELECT min(173,cast((at-?)*174.0/? AS integer)) bin,max(peak) peak
                          FROM levels WHERE meeting=? AND at>=? GROUP BY bin""",
            (origin, width, mid, int(origin)),
        )
        if not points:
            return []
        values = [0.0] * 174
        for p in points:
            values[max(0, p["bin"])] = p["peak"]
        return values

    def finish_job(self, mid, path, segments):
        with self.lock, self.db:
            if not self.db.execute(
                "DELETE FROM jobs WHERE path=? AND meeting=?", (str(path), mid)
            ).rowcount:
                return
            self.db.executemany(
                "INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES(?,'speech',?,?,?,?)",
                [
                    (mid, s.start, s.end, s.speaker or "发言人", s.text)
                    for s in segments
                    if s.text.strip()
                ],
            )

    def notes(self, mid):
        return self.rows("SELECT * FROM notes WHERE meeting=? ORDER BY created DESC,rowid DESC", (mid,))

    _BOARD_FROM = (" FROM topic_notes n JOIN meetings current ON current.topic_id=n.topic "
                   "JOIN meetings source ON source.id=n.source_meeting "
                   "JOIN meetings edited ON edited.id=n.updated_meeting WHERE current.id=?")
    _BOARD_FIELDS = "n.*,source.title AS source_title,edited.title AS updated_title"
    _BOARD_PREVIEW = ("n.id,n.kind,n.title,substr(n.body,1,240) AS body,n.status,n.version,"
                      "n.source_meeting,source.title AS source_title")
    _BOARD_ORDER = "(n.status='done'),n.rowid DESC"
    _FOLLOWUP_WHERE = (" AND n.kind IN ('question','task') AND n.status<>'done' "
                       "AND source.rowid<current.rowid AND NOT EXISTS "
                       "(SELECT 1 FROM topic_reminders r WHERE r.meeting=current.id)")

    def topic_notes(self, mid):
        return self.rows("SELECT " + self._BOARD_FIELDS + self._BOARD_FROM +
                         " ORDER BY " + self._BOARD_ORDER, (mid,))

    def pending_topic_followups(self, mid):
        """Return a bounded opening brief, restricted to unfinished questions and tasks from earlier meetings."""
        return self.rows("SELECT " + self._BOARD_PREVIEW + self._BOARD_FROM + self._FOLLOWUP_WHERE +
                         " ORDER BY n.rowid LIMIT 12", (mid,))

    def save_topic_reminder(self, mid, at, end, text, expected):
        """Commit a visible reminder and its once-per-meeting receipt together."""
        if not text.strip():
            return False
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = [(note["id"], note["version"]) for note in self.pending_topic_followups(mid)]
            if not current or current != list(expected):
                return False
            self.db.execute("INSERT INTO topic_reminders(meeting) VALUES(?)", (mid,))
            self.db.execute("INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES(?,'assistant',?,?,?,?)",
                            (mid, at, end, "听序 AI", text))
            return True

    def _require_topic(self, mid):
        row = self.one("SELECT topic_id FROM meetings WHERE id=?", (mid,))
        if not row or not row["topic_id"]:
            raise ValueError("请先将本场会议归入主题")
        return row["topic_id"]

    def _insert_topic_note(self, mid, topic, values, status, author, source_note=None):
        nid, now = uid(), stamp()
        self.db.execute("INSERT INTO topic_notes(id,topic,kind,title,body,status,source_meeting,source_note,"
                        "updated_meeting,author,updated_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (nid, topic, *values, status, mid, source_note, mid, author, author, now, now))
        return nid

    def _topic_note_by_id(self, mid, nid):
        return self.one("SELECT " + self._BOARD_FIELDS + self._BOARD_FROM + " AND n.id=?", (mid, nid))

    def share_note(self, mid, note_id, author="我"):
        """A meeting note and its shared copy can be edited independently after publication."""
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            topic = self._require_topic(mid)
            local = self.one("SELECT kind,title,body FROM notes WHERE meeting=? AND id=?", (mid, note_id))
            if not local:
                raise ValueError("本场会议中找不到这条便签")
            existing = self.one("SELECT id FROM topic_notes WHERE topic=? AND source_note=?", (topic, note_id))
            if existing:
                return self._topic_note_by_id(mid, existing["id"])
            nid = self._insert_topic_note(mid, topic, (local["kind"], local["title"], local["body"]),
                                          "open", author, source_note=note_id)
            return self._topic_note_by_id(mid, nid)

    def _list_topic_notes(self, mid, args):
        query, status = str(args.get("query", "")).strip()[:500], args.get("status", "all")
        offset = max(0, int(args.get("offset", 0)))
        limit = max(1, min(50, int(args.get("limit", 20))))
        where, values = self._BOARD_FROM, [mid]
        if status != "all":
            if status not in TOPIC_STATUSES:
                raise ValueError("未知的主题便签状态")
            where += " AND n.status=?"
            values.append(status)
        if query:
            where += " AND instr(lower(n.title||' '||n.body),lower(?))>0"
            values.append(query)
        with self.lock:
            self._require_topic(mid)
            return self._history_page(self._BOARD_FIELDS, where, values, self._BOARD_ORDER, offset, limit)

    def _write_topic_note(self, mid, name, args, author):
        status = args.get("status", "open" if name == "create_topic_note" else None)
        if name != "delete_topic_note" and status not in TOPIC_STATUSES:
            raise ValueError("未知的主题便签状态")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            topic = self._require_topic(mid)
            if name == "create_topic_note":
                nid = self._insert_topic_note(mid, topic, self._note_values(args), status, author)
                return self._topic_note_by_id(mid, nid)

            nid = args.get("id", "")
            note = self._topic_note_by_id(mid, nid)
            if not note:
                raise ValueError("当前主题找不到这条便签")
            version = args.get("version")
            if type(version) is not int or version != note["version"]:
                raise ValueError("主题便签已更新，请重新读取后再修改")
            if name == "delete_topic_note":
                self.db.execute("DELETE FROM topic_notes WHERE id=? AND topic=?", (nid, topic))
                return {"deleted": True}
            content = (note["kind"], note["title"], note["body"])
            values = self._note_values(args) if name == "update_topic_note" else content
            if values == content and status == note["status"]:
                return note
            self.db.execute("UPDATE topic_notes SET kind=?, title=?, body=?, status=?, updated_meeting=?, "
                            "updated_by=?, updated=?, version=version+1 WHERE id=? AND topic=?",
                            (*values, status, mid, author, stamp(), nid, topic))
            return self._topic_note_by_id(mid, nid)

    @staticmethod
    def _note_values(args):
        kind = args.get("kind", "idea")
        title = str(args.get("title", "")).strip()[:160]
        body = str(args.get("body", "")).strip()[:16000]
        if kind not in KINDS or not title:
            raise ValueError("请填写便签标题并选择类型")
        return kind, title, body

    # A UUID supplied by a provider never grants access on its own. Both reads use
    # the invoking meeting's membership and insertion order from the database.
    _PRIOR_MEETINGS = ("FROM meetings m JOIN meetings current ON current.topic_id=m.topic_id "
                       "WHERE current.id=? AND m.rowid<current.rowid")

    def _history_page(self, fields, where, args, order, offset, limit):
        count = self.one("SELECT count(*) AS n " + where, args)["n"]
        rows = self.rows("SELECT " + fields + " " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                         (*args, limit, offset))
        return {"total": count, "offset": offset, "items": rows}

    def _topic_history(self, mid, name, args):
        query = str(args.get("query", ""))[:500]
        offset = max(0, int(args.get("offset", 0)))
        limit = max(1, min(50, int(args.get("limit", 10))))
        with self.lock:
            if name == "list_topic_meetings":
                where = self._PRIOR_MEETINGS
                values = [mid]
                if query:
                    where += (" AND (instr(lower(m.title),lower(?))>0 OR EXISTS (SELECT 1 FROM events e "
                              "WHERE e.meeting=m.id AND e.kind='speech' AND instr(lower(e.text),lower(?))>0) "
                              "OR EXISTS (SELECT 1 FROM notes n WHERE n.meeting=m.id "
                              "AND instr(lower(n.title||' '||n.body),lower(?))>0))")
                    values.extend([query] * 3)
                return self._history_page("m.id,m.title,m.created", where, values, "m.rowid DESC", offset, limit)

            target = args.get("meeting_id", "")
            previous = self.one("SELECT m.id,m.title,m.created " + self._PRIOR_MEETINGS + " AND m.id=?", (mid, target))
            if not previous:
                raise ValueError("只能读取当前主题中的往期会议")
            kind = args.get("kind", "")
            if kind == "notes":
                fields = "kind,title,body,author,created"
                where = "FROM notes WHERE meeting=? AND instr(lower(title||' '||body),lower(?))>0"
                order = "created,rowid"
            elif kind in ("transcript", "chat"):
                kinds = "kind='speech'" if kind == "transcript" else "kind IN ('assistant','user')"
                fields = "kind,at AS start,end,speaker,text"
                where = "FROM events WHERE meeting=? AND " + kinds + " AND instr(lower(text||' '||speaker),lower(?))>0"
                order = "at,seq"
            else:
                raise ValueError("请选择转写、便签或 AI 对话记录")
            return {"meeting": previous, **self._history_page(fields, where, (target, query), order, offset, limit)}

    def tool(self, mid, name, args, author="AI"):
        if name == "list_topic_notes":
            return self._list_topic_notes(mid, args)
        if name == "share_note_to_topic":
            return self.share_note(mid, args["id"], author)
        if name in ("create_topic_note", "update_topic_note", "set_topic_note_status", "delete_topic_note"):
            return self._write_topic_note(mid, name, args, author)
        if name in ("list_topic_meetings", "read_topic_history"):
            return self._topic_history(mid, name, args)
        if name == "read_transcript":
            query = str(args.get("query", ""))[:500]
            limit = max(1, min(100, int(args.get("limit", 30))))
            offset = max(0, int(args.get("offset", 0)))
            return self.rows(
                """SELECT at AS start,end,speaker,text FROM events WHERE meeting=? AND kind='speech'
                               AND instr(lower(text||speaker),lower(?))>0 ORDER BY at,seq LIMIT ? OFFSET ?""",
                (mid, query, limit, offset),
            )
        if name == "list_notes":
            return self.notes(mid)
        if name == "delete_note":
            if not self.write(
                "DELETE FROM notes WHERE id=? AND meeting=?", (str(args.get("id", "")), mid)
            ):
                raise ValueError("便签不存在")
            return {"deleted": True}
        if name not in ("create_note", "update_note"):
            raise ValueError("无法识别的会议操作")
        kind, title, body = self._note_values(args)
        nid = uid() if name == "create_note" else str(args.get("id", ""))
        if name == "create_note":
            self.write(
                "INSERT INTO notes VALUES(?,?,?,?,?,?,?)",
                (nid, mid, kind, title, body, author, stamp()),
            )
        elif not self.write(
            "UPDATE notes SET kind=?,title=?,body=? WHERE id=? AND meeting=?",
            (kind, title, body, nid, mid),
        ):
            raise ValueError("便签不存在")
        return {"id": nid, "title": title}

    def seed(self):
        mid = self.create("产品设计周会", True)
        self.write("UPDATE meetings SET duration=184,source_label='示例会议' WHERE id=?", (mid,))
        for t, e, speaker, line in [
            (
                5,
                26,
                "林悦",
                "今天先一起看新版工作台的设计。我们重点确认两个问题：首次使用时，用户先看到什么，以及怎样把讨论中的结论留下来。",
            ),
            (
                31,
                55,
                "周航",
                "我希望会议记录和 AI 对话能放在一起。讨论进行到一半时，可以直接追问，不用切出去找另一个聊天窗口。",
            ),
            (
                68,
                87,
                "林悦",
                "可以，把需要跟进的问题放到右边的便签里。会议结束之后，也能快速回顾今天做的决定。",
            ),
            (
                118,
                141,
                "周航",
                "那我来准备第一版原型。下次评审前，我们找两位同事试用一下，看看他们能不能顺利完成整个流程。",
            ),
        ]:
            self.event(mid, "speech", t, e, speaker, line)
        self.event(
            mid,
            "assistant",
            93,
            93,
            "听序 AI",
            "右侧已记下双栏布局的共识。首次打开工作台时，用户先看记录还是先向 AI 提问？这个问题可以留到原型试用时观察。",
        )
        for kind, title, body, author in [
            ("decision", "保留双栏工作台", "左侧记录会议与 AI 对话，右侧持续整理讨论结论。", "听序 AI"),
            (
                "task",
                "准备第一版可交互原型",
                "周航负责，在下次评审前邀请两位同事试用完整流程。",
                "听序 AI",
            ),
            (
                "question",
                "首次使用时应该先看到什么？",
                "待原型试用后，根据同事的反馈确定默认视图。",
                "林悦",
            ),
        ]:
            self.tool(mid, "create_note", dict(kind=kind, title=title, body=body), author)
