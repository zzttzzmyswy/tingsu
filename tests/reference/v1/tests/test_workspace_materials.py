import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from meeting_agent.agent_materials import make_inputs
from meeting_agent.agent_workspace import MeetingWorkspace
from meeting_agent.storage import Store


class WorkspaceFileTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        with patch("meeting_agent.storage.DATA", self.data):
            self.store = Store()
        self.addCleanup(self.store.db.close)

    def test_old_meetings_joined_topic_and_cloud_keep_originals_scoped(self):
        archived = self.store.create("以前的会议")
        previous_file = self.root / "预算 表.csv"
        first = "项目,数量\n早期,12\n".encode()
        previous_file.write_bytes(first)
        key = hashlib.sha256(("meeting:" + archived).encode()).hexdigest()
        old_id = hashlib.sha256(previous_file.name.encode() + b"\0" + first).hexdigest()
        record = self.data / "materials" / key / old_id
        record.mkdir(parents=True)
        (record / "original").write_bytes(first)
        (record / "name.json").write_text(json.dumps(previous_file.name))

        standalone = MeetingWorkspace(self.data, archived)
        standalone.prepare([archived])
        previous_file.write_bytes(b"new spreadsheet")
        recent = standalone.library(archived).add(previous_file)
        previous_file.unlink()
        self.store.set_meeting_topic(archived, topic_name="产品")
        topic = self.store.meeting(archived)["topic_id"]
        later = self.store.create("继续评审", topic_id=topic)
        other = self.store.create("另一个主题", topic_name="交付")
        work = MeetingWorkspace(self.data, later, topic)
        work.prepare(self.store.workspace_meetings(later))
        work.prepare(self.store.workspace_meetings(later))
        copied = work.library(archived)._get(old_id)
        self.assertEqual(copied.path.read_bytes(), first)
        self.assertEqual(copied.path.name, previous_file.name)
        self.assertEqual(work.library(archived)._get(recent.id).path.read_bytes(), b"new spreadsheet")
        self.assertEqual(len(list(work.directory.rglob(previous_file.name))), 2)
        self.assertEqual(work.library(later).tool("list_attachments", {})["items"], [])

        foreign = MeetingWorkspace(self.data, other, self.store.meeting(other)["topic_id"])
        foreign.prepare(self.store.workspace_meetings(other))
        self.assertFalse(list(foreign.directory.rglob(previous_file.name)))
        self.assertEqual(work.directory, MeetingWorkspace(self.data, archived, topic).directory)

    def test_real_file_paths_only_for_local_agents_and_visual_frame_is_separate(self):
        work = MeetingWorkspace(self.data, "meeting")
        work.prepare(["meeting"])
        source = self.root / "说明 [一].pdf"
        source.write_bytes(b"%PDF-synthetic contents")
        material = work.library("meeting").add(source)
        for protocol in ("claude", "meeting"):
            parts = make_inputs(protocol, "读取文件", [material], "", workspace=work.directory)
            self.assertEqual(len(parts), 1)
            info = json.loads(parts[0]["text"])["attachments"][0]
            self.assertFalse(Path(info["path"]).is_absolute())
            self.assertEqual((work.directory / info["path"]).read_bytes(), source.read_bytes())
            self.assertEqual((work.directory / info["path"]).name, source.name)
            self.assertNotIn("synthetic contents", str(parts))
        parts = make_inputs("openai", "", [material], "")
        self.assertNotIn("path", json.loads(parts[0]["text"])["attachments"][0])
        self.assertEqual(parts[1]["type"], "input_file")
        shot = self.root / "shared.jpg"
        shot.write_bytes(b"visual-test")
        local = make_inputs("meeting", "", [], "", workspace=work.directory, frame=shot)
        self.assertEqual(local[1]["type"], "image")
