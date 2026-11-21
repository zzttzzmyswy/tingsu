import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from meeting_agent.storage import Store


class TopicPersistenceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        data = patch("meeting_agent.storage.DATA", self.directory)
        data.start()
        self.addCleanup(data.stop)

    def open_store(self):
        store = Store()
        self.addCleanup(store.db.close)
        return store

    def test_interleaved_series_keep_separate_sessions_and_histories_across_restart(self):
        store = self.open_store()
        meetings = {"研发": [], "销售": []}
        topic_ids = {}
        for round_number in range(1, 6):
            for name, series in meetings.items():
                args = {"topic_id": topic_ids[name]} if series else {"topic_name": name}
                mid = store.create("周会", **args)
                topic_ids[name] = store.meeting(mid)["topic_id"]
                if round_number == 1:
                    store.bind_agent(mid, "codex", name + " codex", expected_session="")
                    store.bind_agent(mid, "openai", name + " openai", expected_session="")
                store.event(mid, "speech", 1, 2, "主持人", f"{name} 进度 {round_number}")
                store.tool(mid, "create_note", {"kind": "decision", "title": f"{name} 确认 {round_number}"})
                store.event(mid, "user", 2, 2, "我", name + " 私聊")
                store.event(mid, "assistant", 3, 3, "AI", name + " 回复")
                series.append(mid)

        store = self.open_store()
        for name, series in meetings.items():
            other = "销售" if name == "研发" else "研发"
            mid = series[-1]
            view = store.workspace_snapshot(mid, 10)
            self.assertEqual(view["meetingSessions"], {"codex": name + " codex", "openai": name + " openai"})
            self.assertEqual(view["meeting"]["topic_provider"], "openai")
            self.assertEqual({t["name"]: t["meeting_count"] for t in view["topics"]}, {"研发": 5, "销售": 5})
            summary = store.agent_context(mid)
            self.assertEqual([m["id"] for m in summary["往次会议"]], series[:-1][::-1])
            summary_text = json.dumps(summary, ensure_ascii=False)
            self.assertNotIn(other, summary_text)
            self.assertNotIn("私聊", summary_text)
            self.assertNotIn("回复", summary_text)
            self.assertIn(name + " 进度 1", summary_text)
            self.assertIn(name + " 确认 1", summary_text)

            listed = store.tool(mid, "list_topic_meetings", {"query": name + " 确认", "offset": 2, "limit": 1})
            self.assertEqual((listed["total"], listed["offset"], listed["items"][0]["id"]), (4, 2, series[1]))
            self.assertEqual(store.tool(mid, "list_topic_meetings", {"query": "私聊"})["total"], 0)
            for kind, query, count in [("transcript", "进度", 1), ("notes", "确认", 1), ("chat", name, 2)]:
                page = store.tool(mid, "read_topic_history", {"meeting_id": series[0], "query": query, "kind": kind})
                self.assertEqual((page["meeting"]["id"], page["total"]), (series[0], count))
            for source, target in [(mid, meetings[other][0]), (mid, mid), (series[0], mid)]:
                with self.assertRaises(ValueError):
                    store.tool(source, "read_topic_history", {"meeting_id": target, "kind": "transcript"})
            self.assertEqual(store.tool(series[0], "list_topic_meetings", {})["total"], 0)
            # The board itself is owned by a single occurrence even within a topic.
            with self.assertRaises(ValueError):
                store.tool(mid, "delete_note", {"id": store.notes(series[0])[0]["id"]})

    def test_limited_preview_keeps_older_records_searchable(self):
        store = self.open_store()
        first = store.create("First", topic_name="Planning")
        topic = store.meeting(first)["topic_id"]
        store.event(first, "speech", 1, 2, "Engineer", "Earliest estimate 10%_complete")
        for i in range(6):
            mid = store.create(str(i), topic_id=topic)
            store.event(mid, "speech", 0, 1, "Engineer", "x" * 10000)
            store.tool(mid, "create_note", {"title": "y" * 160, "body": "z" * 12000})
        final = store.create("Final", topic_id=topic)
        preview = store.agent_context(final)["往次会议"]
        self.assertEqual(len(preview), 5)
        self.assertNotIn(first, [m["id"] for m in preview])
        self.assertLess(len(json.dumps(preview)), 6000)
        self.assertEqual(store.tool(final, "list_topic_meetings", {"query": "10%_COMPLETE"})["items"][0]["id"], first)
        independent = store.create("Planning")
        self.assertEqual(store.tool(independent, "list_topic_meetings", {})["total"], 0)
        self.assertNotIn("往次会议", store.agent_context(independent))
        with self.assertRaises(ValueError):
            store.tool(independent, "read_topic_history", {"meeting_id": first, "kind": "notes"})

    def test_upgrade_and_archive_do_not_merge_remote_chats(self):
        with sqlite3.connect(self.directory / "studio.sqlite3") as db:
            db.execute("CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, created TEXT, demo INTEGER DEFAULT 0,"
                       "source TEXT DEFAULT '',source_label TEXT DEFAULT '',duration REAL DEFAULT 0,"
                       "agent TEXT DEFAULT '',session TEXT DEFAULT '')")
            db.execute("INSERT INTO meetings(id,title,created,agent,session) "
                       "VALUES('old','Planning','2026-01-01','codex','old-thread')")
        store = self.open_store()
        self.assertIsNone(store.meeting("old")["topic_id"])
        self.assertEqual(store.agent_sessions("old"), {"codex": "old-thread"})
        store.event("old", "speech", 20, 23, "Liu", "Budget estimate")
        new = store.create("Planning", topic_name="Planning")
        other = store.create("Planning", topic_name="Review")
        topic = store.meeting(new)["topic_id"]
        self.assertEqual(store.tool(new, "list_topic_meetings", {})["total"], 0)
        store.set_meeting_topic("old", topic_id=topic)
        self.assertEqual(store.agent_sessions("old"), {})
        self.assertEqual(store.meeting("old")["session"], "")
        store.bind_agent(new, "codex", "series-thread", expected_session="")
        store.bind_agent(new, "codex", "replacement", expected_session="series-thread")
        store = self.open_store()
        self.assertEqual(store.agent_sessions("old"), {"codex": "replacement"})
        self.assertEqual(store.unavailable_session_ids(new, "codex"), {"old-thread"})
        self.assertEqual(store.unavailable_session_ids(other, "codex"), {"old-thread", "series-thread", "replacement"})
        with self.assertRaises(ValueError):
            store.bind_agent(other, "codex", "series-thread")
        standalone = store.create("Standalone")
        with self.assertRaises(ValueError):
            store.bind_agent(standalone, "codex", "replacement")
        with self.assertRaises(ValueError):
            store.bind_agent("old", "codex", "old-thread")
        with self.assertRaises(ValueError):
            store.set_meeting_topic("old", topic_name="must roll back")
        self.assertEqual(len(store.topics()), 2)
        self.assertEqual(store.tool(new, "read_topic_history", {"meeting_id": "old", "kind": "transcript"})
                         ["items"][0]["text"], "Budget estimate")

    def test_concurrent_topic_creation_and_first_binding(self):
        left, right = self.open_store(), self.open_store()
        barrier = Barrier(2)

        def create(store, name):
            barrier.wait()
            return store.create("Weekly", topic_name=name)

        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(create, left, " Product ")
            b = pool.submit(create, right, "product")
            first, second = a.result(), b.result()
        topic = left.meeting(first)["topic_id"]
        self.assertEqual(topic, right.meeting(second)["topic_id"])
        self.assertEqual(len(left.topics()), 1)

        def bind(store, mid, session):
            barrier.wait()
            try:
                store.bind_agent(mid, "openai", session, expected_session="")
                return session
            except ValueError:
                return None

        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(bind, left, first, "one")
            b = pool.submit(bind, right, second, "two")
            winners = [result for result in (a.result(), b.result()) if result]
        self.assertEqual(len(winners), 1)
        for store, mid in [(left, first), (right, second)]:
            self.assertEqual(store.agent_sessions(mid), {"openai": winners[0]})
