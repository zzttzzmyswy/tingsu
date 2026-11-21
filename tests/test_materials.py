"""Full attachments survive restart, format conversion and page boundaries."""

import base64
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from meeting_agent.agent_claude import _content
from meeting_agent.agent_materials import AgentError, MaterialLibrary, make_inputs


class MaterialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = MaterialLibrary(self.root / "meeting-a")

    def test_long_original_is_immutable_searchable_and_scoped_after_restart(self):
        path = self.root / "预算.txt"
        content = "预算🌕。" * 100_000 + "会议交付码 Ω-416"
        path.write_bytes(content.encode())
        first = self.library.add(path)
        self.assertEqual(self.library.add(path).id, first.id)
        listing = self.library.tool("list_attachments", {})
        self.assertEqual(listing["items"], [first.info()])
        small_prompt = make_inputs("meeting", "找交付码", [first], "")
        self.assertLess(len(json.dumps(small_prompt)), 600)
        self.assertNotIn("Ω-416", str(small_prompt))
        path.write_text("用户之后修改的内容", encoding="utf-8")
        second = self.library.add(path)
        self.assertNotEqual(second.id, first.id)
        path.unlink()

        reopened = MaterialLibrary(self.library.directory)
        args = dict(id=first.id, format="text", query="会议交付码", offset=0, limit=128)
        page = reopened.tool("read_attachment", args)
        self.assertEqual(page["content"], "会议交付码 Ω-416")
        self.assertTrue(page["eof"])
        with self.assertRaises(AgentError):
            MaterialLibrary(self.root / "meeting-b").tool("read_attachment", args)
        with self.assertRaises(AgentError):
            reopened.tool("read_attachment", {**args, "id": "../预算.txt"})

        full = []
        cursor = 0
        while True:
            page = reopened.tool("read_attachment", {**args, "offset": cursor, "query":"", "limit":13_931})
            full.append(page["content"])
            cursor = page["next_offset"]
            if page["eof"]:
                break
        self.assertEqual("".join(full), content)
        parts = make_inputs("openai", "看原文件", [first], "")
        self.assertEqual(parts[1]["type"], "input_file")
        self.assertEqual(parts[1]["filename"], "预算.txt")
        self.assertEqual(base64.b64decode(parts[1]["file_data"].split(",")[1]), content.encode())

    def test_large_binary_can_be_read_at_the_end_without_text_extraction(self):
        path = self.root / "录屏数据.bin"
        tail = b"\x00\xffMAGIC"
        with path.open("wb") as out:
            out.seek(26 * 1024 * 1024)
            out.write(tail)
        material = self.library.add(path)
        record = self.library.tool("read_attachment", dict(id=material.id, format="base64", query="",
                                                           offset=-len(tail), limit=100))
        self.assertEqual(base64.b64decode(record["content"]), tail)
        self.assertTrue(record["eof"])

    def test_office_text_and_claude_pdf_are_files(self):
        doc = self.root / "材料.docx"
        with zipfile.ZipFile(doc, "w", compression=zipfile.ZIP_DEFLATED) as package:
            xml = '<w:document xmlns:w="urn:word"><w:p><w:r><w:t>' + "前" * 170_000 + '结束</w:t></w:r></w:p></w:document>'
            package.writestr("word/document.xml", xml)
        entry = self.library.add(doc)
        page = self.library.tool("read_attachment", dict(id=entry.id, format="text", query="结束", offset=0, limit=40))
        self.assertEqual(page["content"], "结束\n")
        pdf = self.root / "diagram.pdf"
        pdf.write_bytes(b"%PDF-test")
        parts = _content(make_inputs("claude", "阅读附件", [self.library.add(pdf)], ""))
        self.assertEqual(parts[1]["type"], "document")
        self.assertEqual(base64.b64decode(parts[1]["source"]["data"]), pdf.read_bytes())
