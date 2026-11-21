"""Exercise historical meeting reads through each agent protocol, including resumed CLIs."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from e2e.deepseek_fixture import TOKEN, start
from meeting_agent.agent_claude import ClaudeBackend
from meeting_agent.agent_codex import CodexBackend
from meeting_agent.agent_deepseek import DeepSeekBackend
from meeting_agent.agent_openai import OpenAIBackend
from meeting_agent.agent_tools import CODEX_OUTPUT, OPENAI_TOOLS, execute
from meeting_agent.storage import Store


LIST = {"query": "", "offset": 0, "limit": 10}
READ = {"meeting_id": "prior-topic-session", "kind": "transcript", **LIST, "query": "预算"}


class TopicToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []

    def history(self, name, args):
        self.calls.append((name, args))
        if name == "list_topic_meetings":
            return {"items": [{"id": "prior-topic-session", "title": "首场"}], "total": 1, "offset": 0}
        if name == "read_topic_history":
            self.assertEqual(args, READ)
            return {"items": [{"start": 9, "text": "预算 417"}], "total": 1, "offset": 0}
        self.fail(f"Unexpected tool {name}")

    def test_paginated_read_schema_for_responses_and_codex(self):
        openai = {tool["name"]: tool["parameters"] for tool in OPENAI_TOOLS}
        codex = CODEX_OUTPUT["properties"]["actions"]["items"]["properties"]["name"]["enum"]
        for name in ("list_topic_meetings", "read_topic_history"):
            self.assertIn(name, codex)
            self.assertFalse(openai[name]["additionalProperties"])
            self.assertEqual(set(openai[name]["required"]), set(openai[name]["properties"]))
        for args in ({}, {**READ, "kind": "files"}, {**READ, "offset": True}, {**READ, "topic_id": "foreign"}):
            self.assertIn("error", json.loads(execute(self.history, "read_topic_history", args)))
        self.assertEqual(self.calls, [])
        parsed = json.loads(execute(self.history, "list_topic_meetings", {"offset": -2, "limit": 4000}))
        self.assertIn("prior-topic-session", str(parsed))
        self.assertEqual(self.calls, [("list_topic_meetings", {"query": "", "offset": 0, "limit": 50})])

    async def test_codex_uses_current_turn_handler_on_resumed_thread(self):
        backend = CodexBackend("")
        async def turn(session, inputs, emit):
            self.assertEqual(session, "existing-thread")
            packet = json.loads(inputs[0]["text"])
            if "request" in packet:
                name, args = "list_topic_meetings", LIST
            else:
                last = packet["meeting_tool_results"][0]
                items = json.loads(last["output"])["result"]["items"]
                if last["name"] == "read_topic_history":
                    return {"message": items[0]["text"], "actions": []}
                self.assertEqual(items[0]["id"], READ["meeting_id"])
                name, args = "read_topic_history", READ
            return {"message": "", "actions": [{"name": name, "arguments": json.dumps(args)}]}
        backend._turn = turn
        inputs = [{"type": "text", "text": json.dumps({"request": "上次预算？"})}]
        self.assertEqual(await backend.send("existing-thread", inputs, lambda _: None, self.history), "预算 417")
        self.assertEqual(self.calls, [("list_topic_meetings", LIST), ("read_topic_history", READ)])

    async def test_responses_includes_history_tools_on_resumed_conversation(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "LOCALAPPDATA": tmp}):
            backend = OpenAIBackend("test-key", "test-model")
            async def stream(payload, emit):
                self.assertEqual(payload["conversation"], "conv_existing")
                self.assertTrue({"list_topic_meetings", "read_topic_history"} <= {t["name"] for t in payload["tools"]})
                item = payload["input"][0]
                if item.get("role") == "user":
                    name, args = "list_topic_meetings", LIST
                else:
                    items = json.loads(item["output"])["result"]["items"]
                    if item["call_id"] == "read_topic_history":
                        return {"output": []}, items[0]["text"]
                    self.assertEqual(items[0]["id"], READ["meeting_id"])
                    name, args = "read_topic_history", READ
                return {"output": [{"type": "function_call", "name": name, "call_id": name,
                                    "arguments": json.dumps(args)}]}, ""
            backend._stream = stream
            inputs = [{"type": "input_text", "text": "上次预算？"}]
            self.assertEqual(await backend.send("conv_existing", inputs, lambda _: None, self.history), "预算 417")
        self.assertEqual(self.calls, [("list_topic_meetings", LIST), ("read_topic_history", READ)])

    @unittest.skipUnless(os.getenv("TINGSU_TEST_DSH"), "set TINGSU_TEST_DSH for the native CLI")
    async def test_real_deepseek_resumes_with_current_meeting_history_tools(self):
        await self.native_recall("deepseek", os.environ["TINGSU_TEST_DSH"])

    @unittest.skipUnless(os.getenv("TINGSU_TEST_CLAUDE"), "set TINGSU_TEST_CLAUDE for the native CLI")
    async def test_real_claude_resumes_with_current_meeting_history_tools(self):
        await self.native_recall("claude", os.environ["TINGSU_TEST_CLAUDE"])

    async def native_recall(self, provider, executable):
        claude = provider == "claude"
        api = start(tool_prefix="mcp__meeting__" if claude else "mcp__tingsu__")
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        url = f"http://127.0.0.1:{api.server_port}"
        with TemporaryDirectory() as tmp, patch.dict(os.environ):
            for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
                os.environ.pop(key, None)
            os.environ.update(XDG_DATA_HOME=tmp, LOCALAPPDATA=tmp, DSH_HOME=str(Path(tmp) / "dsh"),
                              CLAUDE_CONFIG_DIR=str(Path(tmp) / "claude"), DEEPSEEK_BASE_URL=url,
                              DEEPSEEK_API_KEY=TOKEN, ANTHROPIC_BASE_URL=url, ANTHROPIC_API_KEY=TOKEN,
                              CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            backend_type = ClaudeBackend if claude else DeepSeekBackend
            backend = backend_type(executable=executable)
            try:
                await backend.connect()
                sid = await backend.open_session(None, "主题")
                await backend.send(sid, [{"type": "text", "text": "首场会议"}], lambda _: None, None)
            finally:
                await backend.close()
            resumed = backend_type(executable=executable)
            try:
                await resumed.connect()
                self.assertEqual(await resumed.open_session(sid, "次场会议"), sid)
                answer = await resumed.send(sid, [{"type": "text", "text": "tingsu-recall-topic"}],
                                            lambda _: None, self.history)
                self.assertIn("预算 417", answer)
                with patch("meeting_agent.storage.DATA", Path(tmp) / "board-db"):
                    store = Store()
                try:
                    first = store.create("第一场", topic_name="共享测试")
                    next_meeting = store.create("第二场", topic_id=store.meeting(first)["topic_id"])
                    card = store.tool(first, "create_topic_note", dict(kind="task", title="确认预算", body=""))
                    api.board_card = {key: card[key] for key in ("id", "version")}
                    await resumed.send(sid, [{"type": "text", "text": "tingsu-resolve-board"}], lambda _: None,
                                       lambda name, args: store.tool(next_meeting, name, args))
                    updated, = store.topic_notes(first)
                    self.assertEqual((updated["status"], updated["updated_meeting"]), ("done", next_meeting))
                finally:
                    store.db.close()
            finally:
                await resumed.close()
        self.assertEqual(self.calls, [("list_topic_meetings", LIST), ("read_topic_history", READ)])
