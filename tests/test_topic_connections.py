"""Test connection choice, recovery and stale callbacks at the desktop command boundary."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from meeting_agent.bridge import Bridge


class TopicConnections(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for key in ("CONFIG", "DATA"):
            setting = patch("meeting_agent.storage." + key, Path(folder.name) / key)
            setting.start()
            self.addCleanup(setting.stop)
        local_data = patch("meeting_agent.bridge.DATA", Path(folder.name) / "DATA")
        local_data.start()
        self.addCleanup(local_data.stop)
        self.bridge = Bridge(Mock())
        self.addCleanup(self.bridge.store.db.close)
        self.addCleanup(self.bridge.shutdown)
        self.client = Mock()
        self.client.list_sessions.return_value = []
        factory = patch("meeting_agent.bridge.AgentClient", return_value=self.client)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.bridge.work = self.inline

    @staticmethod
    def inline(task, done, failed):
        try:
            result = task()
        except Exception as exc:
            failed(exc)
        else:
            done(result)

    def test_retry_or_explicit_replace_preserves_topic_and_its_provider(self):
        b = self.bridge
        b.cmd_new({"title": "第一次", "topic_name": "产品"})
        b.cmd_connect({"provider": "claude"})
        self.client.open_session.return_value = "thread-one"
        b.cmd_session({})
        topic = b.current()["topic_id"]
        b.cmd_new({"title": "第二次", "topic_id": topic})
        self.client.open_session.side_effect = ValueError("会话已删除")
        b.cmd_connect({})
        self.assertEqual(self.factory.call_args.args, ("claude",))
        self.client.open_session.assert_called_with("thread-one", title="产品")
        self.assertEqual(b.agent_status, "choose")
        self.assertEqual(b.store.agent_sessions(b.mid)["claude"], "thread-one")
        with self.assertRaises(ValueError):
            b.cmd_session({})
        with self.assertRaises(ValueError):
            b.cmd_session({"id": "foreign-thread"})

        self.client.open_session.side_effect = None
        self.client.open_session.return_value = "thread-two"
        b.cmd_session({"replace": True})
        self.assertEqual((b.session, b.agent_status), ("thread-two", "ready"))
        self.assertEqual(b.store.agent_sessions(b.mid)["claude"], "thread-two")

        b.cmd_new({"title": "单次会议"})
        self.client.list_sessions.return_value = [{"id": sid} for sid in ("thread-one", "thread-two", "outside")]
        b.cmd_connect({"provider": "claude"})
        self.assertEqual(b.sessions, [{"id": "outside"}])
        for sid in ("thread-one", "thread-two"):
            with self.assertRaises(ValueError):
                b.cmd_session({"id": sid})

    def test_quick_meeting_switch_drops_delayed_open_and_join_retires_client(self):
        b = self.bridge
        b.cmd_new({"title": "第一次", "topic_name": "交付"})
        a = b.mid
        b.cmd_connect({"provider": "openai"})
        delayed = []
        b.work = lambda task, done, failed: delayed.append(done)
        b.cmd_session({})
        b.cmd_new({"title": "另一场", "topic_name": "评审"})
        delayed[0]("late-thread")
        self.assertFalse(b.store.agent_sessions(a))
        self.assertFalse(b.store.agent_sessions(b.mid))
        self.assertEqual(b.agent_status, "offline")

        b.work = self.inline
        b.cmd_new({"title": "过去的独立会议"})
        self.client.open_session.return_value = "old-personal-thread"
        b.cmd_connect({"provider": "openai"})
        b.cmd_session({})
        b.files = [Path("draft.pdf")]
        b.cmd_set_meeting_topic({"meeting": b.mid, "topic_id": b.store.meeting(a)["topic_id"]})
        self.assertFalse(b.files)
        self.assertEqual(b.agent_status, "offline")
        self.assertFalse(b.store.agent_sessions(b.mid))
