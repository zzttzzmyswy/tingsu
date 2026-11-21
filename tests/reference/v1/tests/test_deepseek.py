"""DeepSeek ACP transport, meeting-tool boundary and optional upstream CLI acceptance."""

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from meeting_agent.agent_deepseek import DeepSeekBackend, _MeetingEndpoint
from meeting_agent.agent_materials import AgentError
from e2e.deepseek_fixture import TOKEN, start


class MeetingEndpointTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = _MeetingEndpoint()
        self.addCleanup(self.endpoint.close)
        self.calls = []
        self.endpoint.handler = lambda name, args: self.calls.append(name) or []

    def request(self, packet, bearer):
        req = Request(self.endpoint.descriptor()["url"], data=json.dumps(packet).encode(),
                      headers={"Authorization": "Bearer " + bearer}, method="POST")
        with urlopen(req, timeout=2) as reply:
            return json.load(reply)

    def test_loopback_requires_token_and_only_current_meeting_tools(self):
        packet = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "list_notes", "arguments": {}}}
        with self.assertRaises(HTTPError) as err:
            self.request(packet, "incorrect")
        self.assertEqual(err.exception.code, 403)
        err.exception.close()
        result = self.request(packet, self.endpoint.token)
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(self.calls, ["list_notes"])
        self.endpoint.handler = None
        self.assertTrue(self.request(packet, self.endpoint.token)["result"]["isError"])


@unittest.skipUnless(os.getenv("TINGSU_TEST_DSH"), "set TINGSU_TEST_DSH to the installed official dsh executable")
class RealDeepSeekTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.api = start(images=True)
        self.profile = self.root / "dsh"
        self.profile.mkdir()
        self.overlay = self.profile / "cordis.patch.yml"
        # dsh only follows the live model catalog for its official API origin. A local
        # endpoint uses the packaged catalog; deepseek-flash advertises vision there.
        self.overlay.write_text(json.dumps([{"id": "acp", "config": {
            "provider": "deepseek-official", "model": "deepseek-flash"
        }}]), encoding="utf-8")
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.addCleanup(self.api.block_release.set)
        env = patch.dict(os.environ, {
            "DSH_HOME": str(self.root / "dsh"), "XDG_DATA_HOME": str(self.root / "data"),
            "LOCALAPPDATA": str(self.root / "data"), "DEEPSEEK_API_KEY": TOKEN,
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{self.api.server_port}",
        })
        env.start()
        self.addCleanup(env.stop)
        self.workspace = self.root / "会议窗口"
        self.backend = DeepSeekBackend(executable=os.environ["TINGSU_TEST_DSH"], workspace=self.workspace)
        self.addAsyncCleanup(self.backend.close)
        await self.backend.connect()

    async def test_materials_tools_image_and_persistent_resume(self):
        sid = await self.backend.open_session(None, "Synthetic")
        attachment = self.workspace / "交付明细 表格.tsv"
        attachment.write_text("编号\t状态\n" + "OLD\tclosed\n" * 20_000 + "ORCHID-718\tready\n",
                              encoding="utf-8")
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGN0a3JjIAUwkaR6VMOohiGlAQBtAQEu4WUadAAAAABJRU5ErkJggg==")
        parts = [{"type": "text", "text": json.dumps({"attachments": [{"path": attachment.name}]}, ensure_ascii=False)},
                 {"type": "image", "url": "data:image/png;base64," + base64.b64encode(png).decode()}]
        calls, deltas = [], []

        def tool(name, arguments):
            calls.append((name, arguments))
            if name == "read_transcript":
                return [{"text": "meeting transcript read by MCP", "at": 12}]
            return {"id": "synthetic-note", **arguments}

        self.assertTrue(self.backend.can_image)
        response = await self.backend.send(sid, parts, deltas.append, tool)
        self.assertEqual(response, "".join(deltas))
        self.assertEqual([n for n, _ in calls], ["read_transcript", "create_note"])
        self.assertEqual(calls[-1][1]["body"], "ORCHID-718")
        self.assertIn(png, self.api.images_received)
        all_requests = json.dumps(self.api.requests)
        self.assertIn("meeting transcript read by MCP", all_requests)
        self.assertNotIn("dsh_session_log", all_requests)
        self.assertNotIn("mcp__tingsu__read_attachment", all_requests)
        await self.backend.close()
        await self.backend.connect()
        self.assertIn(sid, [row["id"] for row in await self.backend.list_sessions()])
        self.assertEqual(sid, await self.backend.open_session(sid, "Resume"))
        self.assertIn("收到", await self.backend.send(sid, [{"type": "text", "text": "hello again"}], lambda _: None, tool))

    async def test_resumed_legacy_session_uses_new_working_directory_and_rejects_other_meetings(self):
        sid = await self.backend.open_session(None, "Legacy")
        await self.backend.send(sid, [{"type": "text", "text": "hello"}], lambda _: None, None)
        await self.backend.close()
        old = self.workspace / "agenda.txt"
        old.write_text("ORCHID-FOREIGN", encoding="utf-8")
        workspace = self.root / "新主题"
        backend = DeepSeekBackend(executable=os.environ["TINGSU_TEST_DSH"], workspace=workspace)
        self.addAsyncCleanup(backend.close)
        await backend.connect()
        (workspace / old.name).write_text("ORCHID-LOCAL", encoding="utf-8")
        self.assertEqual(sid, await backend.open_session(sid, "Resume"))

        async def grep(path):
            self.api.requests.clear()
            message = json.dumps({"attachments": [{"path": str(path)}]}, ensure_ascii=False)
            await backend.send(sid, [{"type": "text", "text": message}], lambda _: None, None)
            self.assertGreaterEqual(len(self.api.requests), 2)
            return json.dumps(self.api.requests[-1]["messages"][-1], ensure_ascii=False)

        self.assertIn("ORCHID-LOCAL", await grep(old.name))
        blocked = await grep(old)
        self.assertNotIn("ORCHID-FOREIGN", blocked)
        self.assertIn("meeting workspace", blocked)
        if os.name != "nt":
            (workspace / "external.txt").symlink_to(old)
            blocked = await grep("external.txt")
            self.assertNotIn("ORCHID-FOREIGN", blocked)
            self.assertIn("meeting workspace", blocked)

    async def test_native_office_conversion_and_csv_read(self):
        document = self.workspace / "合同清单.ods"
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
            archive.writestr("META-INF/manifest.xml", '''<manifest:manifest
                xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
                <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
                <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>''')
            archive.writestr("content.xml", '''<office:document-content office:version="1.2"
                xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
                xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>
                <table:table table:name="合同"><table:table-row><table:table-cell office:value-type="string">
                <text:p>ORCHID-718</text:p></table:table-cell></table:table-row></table:table>
                </office:spreadsheet></office:body></office:document-content>''')
        sid = await self.backend.open_session(None, "Spreadsheet")
        seen = []

        def choose(content, names):
            seen.append(content)
            if len(seen) == 1:
                return "skill", {"name": "office-xlsx"}
            if len(seen) == 2 and "office_convert" in names:
                return "office_convert", {"input": document.name, "format": "csv", "sheet": "合同"}
            if len(seen) == 3:
                text = json.dumps(content, ensure_ascii=False)
                # The original model receives the actual absolute path produced by Office Kit.
                match = re.search(r' bytes\): (.+?\.csv)\\n', text)
                if match:
                    return "read", {"file_path": json.loads('"' + match[1] + '"')}
            return None

        self.api.file_tool = choose
        await self.backend.send(sid, [{"type": "text", "text": document.name}], lambda _: None, None)
        self.assertEqual(len(seen), 4, seen[-1])
        self.assertIn("ORCHID-718", json.dumps(seen[-1]))

    async def test_cancel_reuses_session_and_text_only_route_rejects_image(self):
        await self.backend.close()
        self.api.images = False
        self.overlay.unlink()
        await self.backend.connect()
        self.assertFalse(self.backend.can_image)
        sid = await self.backend.open_session(None, "Synthetic")
        with self.assertRaisesRegex(AgentError, "图片"):
            await self.backend.send(sid, [{"type": "image", "url": "data:image/png;base64,AAAA"}], lambda _: None, None)
        turn = asyncio.create_task(self.backend.send(sid, [{"type": "text", "text": "tingsu-block-request"}], lambda _: None, None))
        self.assertTrue(await asyncio.to_thread(self.api.block_started.wait, 10))
        turn.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(turn, 10)
        self.api.block_release.set()
        text = await self.backend.send(sid, [{"type": "text", "text": "hello again"}], lambda _: None, None)
        self.assertIn("收到", text)
