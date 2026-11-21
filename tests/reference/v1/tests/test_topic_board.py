import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from meeting_agent.storage import Store


class TopicBoardTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        data = patch("meeting_agent.storage.DATA", self.directory)
        data.start()
        self.addCleanup(data.stop)

    def open_store(self):
        store = Store()
        self.addCleanup(store.db.close)
        return store

    def test_shared_board_across_five_meetings_is_scoped_and_meeting_notes_stay_local(self):
        store = self.open_store()
        left = store.create("评审", topic_name="研发")
        right = store.create("评审", topic_name="交付")
        single = store.create("研发")
        local = store.tool(left, "create_note", {"kind": "task", "title": "甲方确认", "body": "周三答复"})
        card = store.share_note(left, local["id"])
        issue = store.tool(left, "create_topic_note", {"kind": "question", "title": "何时上线？"})
        store.tool(right, "create_topic_note", {"kind": "task", "title": "乙方私有待办"})
        self.assertEqual(store.tool(left, "share_note_to_topic", {"id": local["id"]})["id"], card["id"])
        self.assertEqual(store.pending_topic_followups(left), [])
        for private in (single, right):
            self.assertNotIn(card["id"], {n["id"] for n in store.topic_notes(private)})
            with self.assertRaises(ValueError):
                store.tool(private, "set_topic_note_status", {"id": card["id"], "version": 1, "status": "done"})
        for name, args in (("delete_topic_note", {"id": card["id"], "version": 1}),
                           ("update_topic_note", {**card, "body": "外部编辑", "status": "done"}),
                           ("share_note_to_topic", {"id": local["id"]})):
            with self.assertRaises(ValueError):
                store.tool(right, name, args)
        with self.assertRaises(ValueError):
            store.tool(single, "list_topic_notes", {})
        topic = store.meeting(left)["topic_id"]
        for number in range(2, 6):
            later = store.create("评审 " + str(number), topic_id=topic)
            store.create("另一主题评审 " + str(number), topic_id=store.meeting(right)["topic_id"])
            if number == 3:
                store = self.open_store()
            visible = store.workspace_snapshot(later, 10)
            self.assertEqual(visible["notes"], [])
            self.assertEqual({n["id"] for n in visible["topicNotes"]}, {card["id"], issue["id"]})
            self.assertEqual({n["source_title"] for n in visible["topicNotes"]}, {"评审"})
            self.assertEqual({n["id"] for n in store.agent_context(later)["主题共享便签"]}, {card["id"], issue["id"]})
            self.assertEqual({n["id"] for n in store.pending_topic_followups(later)}, {card["id"], issue["id"]})

        updated = store.tool(later, "set_topic_note_status", {"id": card["id"], "version": 1, "status": "in_progress"})
        resolved = store.tool(later, "update_topic_note", {**updated, "body": "周三已签字", "status": "done"})
        self.assertEqual((resolved["source_meeting"], resolved["updated_meeting"], resolved["version"]), (left, later, 3))
        self.assertEqual(resolved["updated_by"], "AI")
        done = store.tool(left, "list_topic_notes", {"status": "done", "query": "三已签"})
        self.assertEqual(done["items"], [resolved])
        self.assertEqual([n["id"] for n in store.pending_topic_followups(later)], [issue["id"]])
        self.assertEqual(store.notes(left)[0]["body"], "周三答复")
        self.assertEqual(store.topic_notes(single), [])
        with self.assertRaises(ValueError):
            store.tool(later, "delete_topic_note", {"id": card["id"], "version": 1})
        reopened = store.tool(later, "set_topic_note_status", {**resolved, "status": "open"})
        self.assertEqual((reopened["status"], reopened["version"]), ("open", 4))
        store.tool(left, "delete_topic_note", {"id": card["id"], "version": 4})
        self.assertEqual([n["id"] for n in store.topic_notes(later)], [issue["id"]])

    def test_opening_reminder_commits_once_and_only_with_current_eligible_revisions(self):
        store = self.open_store()
        first = store.create("第一场", topic_name="发布")
        topic = store.meeting(first)["topic_id"]
        current = store.create("第二场", topic_id=topic)
        future = store.create("第三场", topic_id=topic)
        one = store.tool(first, "create_topic_note", {"kind": "task", "title": "验收"})
        two = store.tool(first, "create_topic_note", {"kind": "question", "title": "预算"})
        for mid, kind, status in [(first, "idea", "open"), (first, "decision", "open"), (first, "question", "done"),
                                  (current, "question", "open"), (future, "question", "open")]:
            store.tool(mid, "create_topic_note", {"kind": kind, "title": "不应进入此次提醒", "status": status})
        def signature(mid):
            return [(n["id"], n["version"]) for n in store.pending_topic_followups(mid)]
        before = signature(current)
        self.assertEqual(before, [(one["id"], 1), (two["id"], 1)])
        store.tool(first, "set_topic_note_status", {**one, "status": "done"})
        self.assertFalse(store.save_topic_reminder(current, 1, 1, "过期内容", before))
        expected = signature(current)
        self.assertEqual(expected, [(two["id"], 1)])
        self.assertFalse(store.save_topic_reminder(current, 1, 1, " ", expected))
        self.assertFalse(store.events(current))
        db2 = self.open_store()
        barrier = Barrier(2)
        def submit(conn):
            barrier.wait()
            return conn.save_topic_reminder(current, 2, 3, "请确认首场留下的预算问题", expected)
        with ThreadPoolExecutor(2) as pool:
            results = [pool.submit(submit, conn) for conn in (store, db2)]
            self.assertCountEqual([result.result() for result in results], [True, False])
        resumed = self.open_store()
        self.assertEqual(resumed.pending_topic_followups(current), [])
        self.assertFalse(resumed.save_topic_reminder(current, 2, 3, "重复提醒", expected))
        self.assertEqual([e["text"] for e in resumed.events(current)], ["请确认首场留下的预算问题"])
        self.assertIn(two["id"], [n["id"] for n in resumed.pending_topic_followups(future)])

    def test_paging_preview_and_concurrent_edit_do_not_lose_an_update(self):
        store = self.open_store()
        first = store.create("第一场", topic_name="大看板")
        later = store.create("第二场", topic_id=store.meeting(first)["topic_id"])
        for number in range(55):
            store.tool(first, "create_topic_note", {"kind": "task", "title": f"问题 {number}", "body": "长" * 1000})
        page = store.tool(later, "list_topic_notes", {"query": "问题", "offset": 50, "limit": 50})
        self.assertEqual((page["total"], len(page["items"])), (55, 5))
        self.assertEqual([n["title"] for n in page["items"]], ["问题 4", "问题 3", "问题 2", "问题 1", "问题 0"])
        self.assertEqual(len(store.agent_context(later)["主题共享便签"]), 20)
        pending = store.pending_topic_followups(later)
        self.assertEqual(len(pending), 12)
        self.assertTrue(all(len(n["body"]) == 240 for n in pending))
        note = page["items"][0]
        other = self.open_store()
        barrier = Barrier(2)
        def update(conn, status):
            barrier.wait()
            try:
                return conn.tool(later, "set_topic_note_status", {**note, "status": status})["status"]
            except ValueError:
                return None
        with ThreadPoolExecutor(2) as pool:
            r1 = pool.submit(update, store, "in_progress")
            r2 = pool.submit(update, other, "done")
            winners = [value for value in (r1.result(), r2.result()) if value]
        self.assertEqual(len(winners), 1)
        self.assertEqual(store.tool(later, "list_topic_notes", {"query": "问题 4", "status": winners[0]})["items"][0]["version"], 2)

    def test_upgrade_preserves_single_meeting_notes_until_explicitly_shared(self):
        with sqlite3.connect(self.directory / "studio.sqlite3") as db:
            db.executescript("""
              CREATE TABLE topics(id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE, last_provider TEXT DEFAULT '');
              INSERT INTO topics(id,name,name_key) VALUES('t','评审','评审');
              CREATE TABLE meetings(id TEXT PRIMARY KEY, title TEXT, created TEXT, demo INTEGER DEFAULT 0,
                 source TEXT DEFAULT '',source_label TEXT DEFAULT '',duration REAL DEFAULT 0,agent TEXT DEFAULT '',
                 session TEXT DEFAULT '',origin REAL DEFAULT 0,topic_id TEXT REFERENCES topics(id));
              INSERT INTO meetings(id,title,created,topic_id) VALUES('m','上次会议','2026-01-01','t');
              CREATE TABLE notes(id TEXT PRIMARY KEY,meeting TEXT REFERENCES meetings(id),kind TEXT,title TEXT,
                                 body TEXT,author TEXT,created TEXT);
              INSERT INTO notes VALUES('local','m','task','跟进合同','签字','我','2026-01-01');
            """)
        store = self.open_store()
        next_mid = store.create("下一场", topic_id="t")
        self.assertEqual(store.notes("m")[0]["body"], "签字")
        self.assertEqual(store.topic_notes(next_mid), [])
        card = store.share_note("m", "local")
        store.tool("m", "delete_note", {"id": "local"})
        self.assertEqual(store.pending_topic_followups(next_mid)[0]["id"], card["id"])
        self.assertEqual(self.open_store().topic_notes(next_mid)[0]["body"], "签字")
