import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from meeting_agent.agent_codex import _visible_prefix
from meeting_agent.agent_materials import AgentError
from meeting_agent.agents import AgentClient


def events(*values):
    stream = "".join(f"data: {json.dumps(value)}\n\n" for value in values)
    return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})


class AgentTests(unittest.TestCase):
    def test_codex_stream_hides_actions_and_handles_split_escapes(self):
        message = '会议“开始”\n\\ "🟢"'
        raw = json.dumps({"message": message, "actions": [{"name": "delete_note"}]})
        prefix = ""
        for end in range(len(raw) + 1):
            current = _visible_prefix(raw[:end])
            self.assertTrue(current.startswith(prefix))
            self.assertTrue(message.startswith(current))
            prefix = current
        self.assertEqual(prefix, message)

    def test_openai_tool_result_survives_disconnect_and_resume(self):
        seen = []
        notes = []

        def transport(request):
            path = request.url.path
            if path.endswith("/models/gpt-5-mini"):
                return httpx.Response(200, json={"id": "gpt-5-mini"})
            if path.endswith(("/conversations", "/conversations/conv_synthetic")):
                return httpx.Response(
                    200, json={"id": "conv_synthetic", "metadata": {"title": "Synthetic"}}
                )
            if path.endswith("/items"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"type": "function_call", "call_id": "call_synthetic"},
                            {"role": "user", "type": "message"},
                        ]
                    },
                )
            self.assertTrue(path.endswith("/responses"))
            body = json.loads(request.content)
            seen.append(body)
            self.assertEqual(body["conversation"], "conv_synthetic")
            if len(seen) == 1:
                self.assertIn("agenda.txt", str(body["input"]))
                self.assertTrue(any(v["type"] == "input_image" for v in body["input"][0]["content"]))
                return events(
                    {
                        "type": "response.completed",
                        "response": {
                            "output": [
                                {
                                    "type": "function_call",
                                    "call_id": "call_synthetic",
                                    "name": "create_note",
                                    "arguments": json.dumps(
                                        {"kind": "idea", "title": "hello", "body": ""}
                                    ),
                                }
                            ]
                        },
                    }
                )
            self.assertEqual(body["input"][0]["type"], "function_call_output")
            self.assertIn("note_synthetic", body["input"][0]["output"])
            if len(seen) == 2:
                return events()  # Socket closes before completion is acknowledged.
            return events(
                {"type": "response.output_text.delta", "delta": "完"},
                {"type": "response.output_text.delta", "delta": "成"},
                {"type": "response.completed", "response": {"output": []}},
            )

        def client():
            agent = AgentClient("openai", api_key="unit-test")
            agent._backend.http = httpx.AsyncClient(
                base_url="https://example.invalid/v1/", transport=httpx.MockTransport(transport)
            )
            self.addCleanup(agent.close)
            agent.connect()
            return agent

        def add_note(name, args):
            self.assertEqual(name, "create_note")
            notes.append(args)
            return {"id": "note_synthetic"}

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, XDG_DATA_HOME=root):
            file = Path(root) / "agenda.txt"
            file.write_text("Synthetic material", encoding="utf-8")
            image = Path(root) / "screen.png"
            image.write_bytes(b"synthetic image transport fixture")
            agent = client()
            self.assertEqual(agent.open_session(), "conv_synthetic")
            with self.assertRaisesRegex(AgentError, "提前结束"):
                agent.send("Add the note.", [file, image], tool_handler=add_note)
            agent.close()
            resumed = client()
            self.assertEqual(resumed.list_sessions(), [{"id": "conv_synthetic", "title": "Synthetic"}])
            resumed.open_session("conv_synthetic")
            deltas = []
            self.assertEqual(
                resumed.send("Continue.", on_delta=deltas.append, tool_handler=add_note), "完成"
            )
            self.assertEqual(deltas, ["完", "成"])
            self.assertEqual(len(notes), 1)
            self.assertEqual(len(seen), 3)
