"""Exercise the same threaded scheduler as the desktop without importing Qt."""

import json
import queue
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from meeting_agent.agent_materials import AgentCancelled
from meeting_agent.storage import Store
from meeting_agent.turn_service import AUTO_PROMPT, TurnService


class ControlledClient:
    def __init__(self):
        self.calls = queue.Queue()
        self.cancels = 0

    def send(self, prompt, **kwargs):
        result = queue.Queue()
        self.calls.put(dict(prompt=prompt, result=result.put, **kwargs))
        reply = result.get(timeout=3)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def cancel(self):
        self.cancels += 1


class TurnServiceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch("meeting_agent.storage.DATA", Path(directory.name)):
            self.store = Store()
        self.addCleanup(self.store.db.close)
        self.mid = self.store.create("需求讨论")
        self.other = self.store.create("其他会议")
        self.callbacks = queue.Queue()
        self.streams, self.notices = [], []
        self.client = ControlledClient()
        self.turns = TurnService(
            self.store, post=self.callbacks.put, position=lambda: 125,
            prepare_frame=lambda mid, at: lambda: Path("screen.jpg"), changed=lambda: None,
            stream=self.streams.append, notify=lambda *msg: self.notices.append(msg), automatic=True,
        )
        self.turns.bind(self.mid, self.client, 120)

    def pump_until(self, condition):
        until = time.monotonic() + 3
        while not condition():
            if time.monotonic() > until:
                self.fail("Owner thread did not receive the expected update")
            try:
                callback = self.callbacks.get(timeout=0.01)
            except queue.Empty:
                continue
            callback()

    def next_call(self):
        self.pump_until(lambda: not self.client.calls.empty())
        return self.client.calls.get_nowait()

    def test_user_fifo_precedes_coalesced_analysis_and_context_is_current(self):
        self.store.event(self.mid, "speech", 119, 120, "发言人 01", "本周发布")
        self.turns.transcribed(self.other, 121, True)
        self.turns.transcribed(self.mid, 121, False)
        self.assertFalse(self.turns.sending)
        self.turns.transcribed(self.mid, 123, True)
        auto = self.next_call()
        self.assertEqual(auto["prompt"], AUTO_PROMPT)
        self.assertEqual(json.loads(auto["context"])["近期发言"][0]["text"], "本周发布")

        files = [Path("agenda.txt")]
        self.turns.submit("  第一问  ", files)
        files.clear()
        self.turns.submit("第二问", [])
        self.assertEqual(self.turns.queued, 2)
        self.turns.transcribed(self.mid, 151, True)
        self.turns.transcribed(self.mid, 195, True)
        note = auto["tool_handler"]("create_note", dict(title="发布日期", kind="task", body="确认日期"))
        auto["result"]("整理完毕")
        first = self.next_call()
        self.assertEqual(first["prompt"], "第一问")
        self.assertEqual(first["attachments"], [Path("agenda.txt"), Path("screen.jpg")])
        self.assertEqual(json.loads(first["context"])["当前便签"][0]["id"], note["id"])
        first["result"]("第一答")
        second = self.next_call()
        self.assertEqual(second["prompt"], "第二问")
        second["result"]("第二答")
        coalesced = self.next_call()
        self.assertEqual(coalesced["prompt"], AUTO_PROMPT)
        coalesced["result"]("已更新")
        self.pump_until(lambda: not self.turns.sending)
        replies = [e["text"] for e in self.store.events(self.mid) if e["kind"] == "assistant"]
        self.assertEqual(replies, ["整理完毕", "第一答", "第二答", "已更新"])
        self.assertEqual(self.turns.queued, 0)

    def test_cancel_keeps_partial_reply_advances_user_and_retires_tools(self):
        self.turns.submit("第一个任务", [])
        old = self.next_call()
        old["on_delta"]("半句")
        self.pump_until(lambda: "半句" in self.streams)
        self.turns.transcribed(self.mid, 140, True)
        self.turns.submit("", [Path("report.pdf")])
        self.turns.cancel()
        self.assertTrue(old["cancel_event"].is_set())
        self.assertEqual(self.client.cancels, 1)
        with self.assertRaises(AgentCancelled):
            old["tool_handler"]("create_note", dict(title="旧操作", body="", kind="idea"))
        old["result"]("供应商在取消后返回了整段内容")
        new = self.next_call()
        self.assertEqual(new["prompt"], "请阅读这些会议材料。")
        old["on_delta"]("迟到的上一条回复")
        new["on_delta"]("新回复")
        new["result"]("新回复")
        self.pump_until(lambda: not self.turns.sending)
        self.assertNotIn("迟到的上一条回复", "".join(self.streams))
        replies = [e["text"] for e in self.store.events(self.mid) if e["kind"] == "assistant"]
        self.assertEqual(replies, ["半句\n（回复已停止）", "新回复"])
        self.assertEqual(self.store.notes(self.mid), [])
        self.assertEqual(self.client.calls.qsize(), 0)

    def test_switch_drops_old_callbacks_and_disabled_analysis_stays_off(self):
        self.turns.submit("前场会议", [])
        old = self.next_call()
        self.turns.submit("前场排队消息", [])
        self.turns.bind(self.other, self.client, 0)
        self.assertEqual(self.turns.queued, 0)
        self.turns.submit("新会议", [])
        new = self.next_call()
        old["on_delta"]("旧会议")
        old["result"]("旧会议")
        with self.assertRaises(AgentCancelled):
            old["tool_handler"]("list_notes", {})
        self.turns.transcribed(self.other, 3, True)
        self.turns.set_automatic(False)
        new["result"](ValueError("模拟断线"))
        self.pump_until(lambda: not self.turns.sending)
        self.turns.transcribed(self.other, 400, True)
        self.assertFalse(self.turns.sending)
        self.assertNotIn("旧会议", "".join(self.streams))
        self.assertEqual([e["kind"] for e in self.store.events(self.mid)], ["user", "user"])
        self.assertEqual([e["kind"] for e in self.store.events(self.other)], ["user", "system"])
        self.assertIn(("模拟断线", "error"), self.notices)
