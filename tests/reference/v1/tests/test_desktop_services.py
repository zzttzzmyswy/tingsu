import base64
import os
from pathlib import Path
from queue import Queue
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from meeting_agent.codex_login import CodexLogin
from meeting_agent.library import _has_picture, thumbnail
from meeting_agent.windows_capture import _jpeg, list_windows


class DesktopMediaTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "requires FFmpeg")
    def test_capture_bmp_to_jpeg_and_thumbnail_color(self):
        def bitmap(w, h, bgr):
            stride = (w * 3 + 3) & ~3
            pixels = (bgr * w + b"\0" * (stride - w * 3)) * h
            return (b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54) +
                    struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixels), 0, 0, 0, 0) + pixels)
        with tempfile.TemporaryDirectory() as tmp:
            cover = Path(tmp) / "缩略图.jpg"
            cover.write_bytes(_jpeg(bitmap(2000, 250, bytes([0, 0, 220]))))
            self.assertEqual(cover.read_bytes()[:2], b"\xff\xd8")
            dim = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v",
                                           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(cover)])
            self.assertEqual(dim.strip(), b"1920,240")
            self.assertTrue(_has_picture(cover))
            preview = thumbnail(cover, 160)
            self.assertTrue(preview.startswith("data:image/jpeg;base64,"))
            self.assertTrue(base64.b64decode(preview.split(",")[1]).startswith(b"\xff\xd8"))
            cover.write_bytes(_jpeg(bitmap(160, 64, b"\0\0\0")))
            self.assertFalse(_has_picture(cover))
            self.assertEqual(thumbnail(Path(tmp) / "missing"), "")

    def test_windows_list_keeps_identifiers_and_excludes_own_process(self):
        with patch("meeting_agent.windows_capture._rows", return_value=[
            {"id": f"h1/{os.getpid()}/start", "label": "工作台"},
            {"id": "h2/1/start", "label": "周会"},
        ]):
            self.assertEqual(list_windows(), [{"id": "h2/1/start", "title": "周会", "app": "h2"}])


@unittest.skipIf(os.name == "nt", "uses a POSIX CLI fixture")
class CodexLoginTests(unittest.TestCase):
    def test_link_completion_and_cancelling_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "codex"
            cli.write_text('#!/bin/sh\necho "$$" > "${0}.pid"\necho "https://auth.openai.com/synthetic"\n'
                           'while [ ! -f "${0}.allow" ]; do sleep 0.05; done\n')
            cli.chmod(0o700)
            posted, urls, statuses = Queue(), [], []
            service = CodexLogin(post=posted.put, finished=statuses.append, link_available=lambda: urls.append(service.url))
            self.addCleanup(service.stop)
            service.start(str(cli))
            posted.get(timeout=10)()
            self.assertEqual(urls, ["https://auth.openai.com/synthetic"])
            pid = int((root / "codex.pid").read_text())
            service.stop()
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            # A callback already enqueued by the retired attempt must not affect the replacement.
            service.start(str(cli))
            (root / "codex.allow").touch()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not statuses:
                posted.get(timeout=10)()
            self.assertEqual(statuses, [True])
            self.assertIsNone(service.url)
