import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from meeting_agent.asr import Segment
from meeting_agent.storage import Store


class MeetingPersistenceTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.directory = Path(root.name)
        data = patch("meeting_agent.storage.DATA", self.directory)
        data.start()
        self.addCleanup(data.stop)
        self.store = Store()
        self.addCleanup(self.store.db.close)
        self.mid = self.store.create("Meeting A")

    def test_existing_binding_migrates_and_survives_switching_providers(self):
        self.store.write("UPDATE meetings SET agent='codex',session='old-thread' WHERE id=?", (self.mid,))
        reopened = Store()
        self.addCleanup(reopened.db.close)
        reopened.bind_agent(self.mid, "claude", "claude-thread")
        reopened.bind_agent(self.mid, "deepseek", "deepseek-thread")
        again = Store()
        self.addCleanup(again.db.close)
        self.assertEqual(again.agent_sessions(self.mid), {
            "codex": "old-thread", "claude": "claude-thread", "deepseek": "deepseek-thread"
        })
        self.assertEqual(again.meeting(self.mid)["agent"], "deepseek")
        self.assertEqual(again.agent_sessions(again.create("Other meeting")), {})

    def test_pending_audio_survives_restart_and_commits_only_once(self):
        audio = self.directory / "chunk.wav"
        with wave.open(str(audio), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x20" * 32000)
        self.store.queue(self.mid, audio, 300, 302)
        self.store.event(self.mid, "user", 302, 302, "我", "请记下")
        resumed = Store()
        self.addCleanup(resumed.db.close)
        self.assertEqual(resumed.meeting(self.mid)["duration"], 302)
        self.assertEqual(
            resumed.one("SELECT start FROM jobs WHERE meeting=?", (self.mid,))["start"], 300
        )
        segments = [Segment(300.2, 301.8, "发言人 01", "下一站")]
        for _ in range(2):
            resumed.finish_job(self.mid, audio, segments)
        self.assertEqual([e["kind"] for e in resumed.events(self.mid)], ["speech", "user"])
        self.assertEqual(resumed.rows("SELECT * FROM jobs"), [])
        self.assertTrue(all(0 < n <= 1 for n in resumed.levels(self.mid, 300, 302)))
        history = resumed.tool(self.mid, "read_transcript", {"query": "下一站"})
        self.assertEqual([s["start"] for s in history], [300.2])

    def test_agent_cannot_change_another_meetings_note(self):
        other = self.store.create("Meeting B")
        note = dict(kind="task", title="议题", body="议程正文")
        saved = self.store.tool(self.mid, "create_note", note)
        self.assertEqual(self.store.tool(other, "list_notes", {}), [])
        for op in ("update_note", "delete_note"):
            with self.assertRaises(ValueError):
                self.store.tool(other, op, {**note, "id": saved["id"]})
        self.assertEqual(self.store.notes(self.mid)[0]["title"], "议题")

    def test_search_pages_include_early_events_and_respect_filters_and_meeting(self):
        other = self.store.create("Other")
        self.store.event(other, "speech", 0, 0, "同事", "10%_done")
        for i in range(128):
            self.store.event(self.mid, "speech", i // 2, i // 2 + 1, "同事", f"10%_done {i}")
        self.store.event(self.mid, "user", 2, 2, "我", "10%_DONE")
        self.store.event(self.mid, "speech", 1, 1, "另一位", "10xxxxdone")
        first = self.store.search_events(self.mid, "10%_dOne", "同事", "speech", 0)
        second = self.store.search_events(self.mid, "10%_dOne", "同事", "speech", 60)
        last = self.store.search_events(self.mid, "10%_dOne", "同事", "speech", 120)
        self.assertEqual([first[0], second[0], last[0]], [128] * 3)
        self.assertEqual([r["text"] for r in first[1] + second[1] + last[1]], [f"10%_done {i}" for i in range(128)])
        total, rows = self.store.search_events(self.mid, "", "", "ai", 0)
        self.assertEqual((total, [r["kind"] for r in rows]), (1, ["user"]))
        self.assertEqual(self.store.search_events(self.mid, "", "另一位", "all", 0)[0], 1)
        self.assertEqual(self.store.search_events(self.mid, "10%_dOne", "", "all", 0)[0], 129)

    def test_workspace_and_agent_read_different_windows_of_the_same_meeting(self):
        for i in range(45):
            self.store.event(self.mid, "speech", i, i + 1, "甲", str(i))
            self.store.tool(self.mid, "create_note", dict(kind="idea", title=str(i)))
        self.store.event(self.mid, "user", 45, 45, "我", "提问")
        view = self.store.workspace_snapshot(self.mid, 12)
        context = self.store.agent_context(self.mid)
        self.assertTrue(view["older"])
        self.assertEqual(view["events"][-1]["text"], "提问")
        self.assertEqual(len(view["events"]), 12)
        self.assertEqual(view["speakers"][0]["seconds"], 45)
        self.assertEqual([e["text"] for e in context["近期发言"]], [str(i) for i in range(13, 45)])
        self.assertEqual([n["title"] for n in context["当前便签"]], [str(i) for i in range(44, 4, -1)])
