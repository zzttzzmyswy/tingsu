from __future__ import annotations

import json
import os
import sqlite3
import threading
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
        model="",
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
          CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, demo INTEGER DEFAULT 0,
            source TEXT DEFAULT '', source_label TEXT DEFAULT '', duration REAL DEFAULT 0,
            agent TEXT DEFAULT '', session TEXT DEFAULT '');
          CREATE TABLE IF NOT EXISTS meeting_agents (
            meeting TEXT REFERENCES meetings(id) ON DELETE CASCADE, provider TEXT NOT NULL,
            session TEXT NOT NULL, PRIMARY KEY(meeting, provider));
          INSERT OR IGNORE INTO meeting_agents(meeting,provider,session)
            SELECT id,agent,session FROM meetings WHERE agent<>'' AND session<>'';
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, meeting TEXT REFERENCES meetings(id), kind TEXT NOT NULL,
            at REAL NOT NULL, end REAL NOT NULL, speaker TEXT NOT NULL, text TEXT NOT NULL, files TEXT DEFAULT '[]');
          CREATE INDEX IF NOT EXISTS events_meeting ON events(meeting,seq);
          CREATE INDEX IF NOT EXISTS events_meeting_time ON events(meeting,at,seq);
          CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, meeting TEXT REFERENCES meetings(id), kind TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (
            path TEXT PRIMARY KEY, meeting TEXT REFERENCES meetings(id), start REAL, end REAL);
          CREATE TABLE IF NOT EXISTS levels (
            meeting TEXT REFERENCES meetings(id), at INTEGER, peak REAL, PRIMARY KEY(meeting, at));
          CREATE TABLE IF NOT EXISTS media (path TEXT PRIMARY KEY);
        """)
        if "origin" not in {r["name"] for r in self.rows("PRAGMA table_info(meetings)")}:
            self.write("ALTER TABLE meetings ADD COLUMN origin REAL NOT NULL DEFAULT 0")
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

    def create(self, title, demo=False, *, source="", source_label="", origin=0):
        mid = uid()
        self.write(
            "INSERT INTO meetings(id,title,created,demo,source,source_label,origin,duration) "
            "VALUES (?,?,?,?,?,?,?,?)", (mid, title, stamp(), demo, source, source_label, origin, origin)
        )
        return mid

    def meeting(self, mid):
        return self.one("SELECT * FROM meetings WHERE id=?", (mid,))

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
                meetings=self.rows("SELECT * FROM meetings ORDER BY demo ASC,created DESC,rowid DESC"),
                meeting=meeting, events=events[-limit:], older=len(events) > limit,
                trackSpeech=self.rows("SELECT at,end,speaker FROM events WHERE meeting=? AND kind='speech' "
                                      "ORDER BY at,seq", (mid,)),
                speakers=self.rows("SELECT speaker,sum(max(0,end-at)) seconds,min(seq) first FROM events "
                                   "WHERE meeting=? AND kind='speech' GROUP BY speaker ORDER BY first", (mid,)),
                levels=self.levels(mid, meeting["origin"], meeting["duration"]), notes=self.notes(mid),
                pending=self.pending_count(mid), meetingSessions=self.agent_sessions(mid),
            )

    def agent_context(self, mid):
        with self.lock:
            meeting = self.meeting(mid)
            recent = self.rows("SELECT at,speaker,text FROM events WHERE meeting=? AND kind='speech' "
                               "ORDER BY at DESC,seq DESC LIMIT 32", (mid,))
            notes = self.rows("SELECT id,kind,title,body FROM notes WHERE meeting=? "
                              "ORDER BY created DESC,rowid DESC LIMIT 40", (mid,))
            return {"会议": meeting["title"], "示例": bool(meeting["demo"]),
                    "近期发言": recent[::-1], "当前便签": notes}

    def bind_agent(self, mid, provider, session):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO meeting_agents VALUES(?,?,?) ON CONFLICT(meeting, provider) "
                "DO UPDATE SET session=excluded.session", (mid, provider, session)
            )
            self.db.execute("UPDATE meetings SET agent=?,session=? WHERE id=?", (provider, session, mid))

    def agent_sessions(self, mid):
        return {r["provider"]: r["session"] for r in
                self.rows("SELECT provider,session FROM meeting_agents WHERE meeting=?", (mid,))}

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

    def tool(self, mid, name, args, author="AI"):
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
        kind = args.get("kind", "idea")
        title = str(args.get("title", "")).strip()[:160]
        body = str(args.get("body", "")).strip()[:16000]
        if kind not in KINDS or not title:
            raise ValueError("请填写便签标题并选择类型")
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
