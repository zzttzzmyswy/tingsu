"""Regression against the Windows single file, system WebView2, real dialogs and local models."""

from base64 import b64decode
from http.server import ThreadingHTTPServer
from io import BytesIO
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from threading import Event, Thread

from PIL import Image
from desktop import wait
from meeting_topics import TOPICS
from native_windows_codex import choose_attachment, desktop
from queued_turns import SlowModel, check as check_turns
from topic_board import BoardModel, board_flow
from workspace_tools import check as check_workspace, seed


def synthetic_avi(target):
    # Uncompressed BGR24. The release's ffprobe/FFmpeg inspect and decode it.
    width, height = 120, 80
    frame_size = width * height * 3
    def chunk(tag, data):
        return tag + struct.pack("<I", len(data)) + data + bytes(len(data) % 2)
    main = struct.pack("<14I", 1_000_000, frame_size, 0, 0, 2, 0, 1, frame_size, width, height, 0, 0, 0, 0)
    stream = struct.pack("<4s4sIHH8I4h", b"vids", b"DIB ", 0, 0, 0, 0, 1, 1, 0, 2,
                         frame_size, 0xFFFFFFFF, 0, 0, 0, width, height)
    bitmap = struct.pack("<IiiHH6I", 40, width, height, 1, 24, 0, frame_size, 0, 0, 0, 0)
    streams = chunk(b"LIST", b"strl" + chunk(b"strh", stream) + chunk(b"strf", bitmap))
    header = chunk(b"LIST", b"hdrl" + chunk(b"avih", main) + streams)
    pixels = chunk(b"00db", bytes((24, 99, 200)) * width * height)
    target.write_bytes(chunk(b"RIFF", b"AVI " + header + chunk(b"LIST", b"movi" + pixels + pixels)))


def profile(root):
    env = {k: v for k, v in os.environ.items() if not any(word in k for word in ("API_KEY", "TOKEN"))}
    folders = {"LOCALAPPDATA": "local", "APPDATA": "roaming", "USERPROFILE": "user"}
    for key, name in folders.items():
        target = root / name
        target.mkdir(parents=True)
        env[key] = str(target)
    env.update(HOME=env["USERPROFILE"], TINGSU_APP_DIR=str(root))
    return env


def media_flow(page, root):
    source = root / "彩色 小样.avi"
    synthetic_avi(source)
    page.click("library")
    page.click("import")
    choose_attachment(source, "添加会议录屏或音频")
    page.click("media", "彩色 小样")
    page.field("#meeting-name", "原生 Windows 媒体验收")
    page.click("create")
    page.until("S.meeting.title==='原生 Windows 媒体验收'")
    page.click("screen")
    page.click("snapshot")
    page.until("S.frame?.startsWith('data:image/')")
    with Image.open(BytesIO(b64decode(page.js("S.frame").split(",", 1)[1]))) as image:
        center = image.convert("RGB").getpixel((image.width//2, image.height//2))
        assert abs(image.width / image.height - 1.5) < 0.02 and all(
            abs(a - b) < 5 for a, b in zip(center, (200, 99, 24))), (center, image.size)
    page.click("close", area="#dialog ")
    dest = root / "真实导出.md"
    page.click("export")
    choose_attachment(dest, "导出会议记录")
    wait(dest.exists, "Markdown export")
    assert "原生 Windows 媒体验收" in dest.read_text(encoding="utf-8")
    print("PASS native open/save dialogs, real AVI import and decoded frame, Markdown export", flush=True)


def verify_regression(app):
    with TemporaryDirectory(prefix="tingsu-webview2-workspace-") as temp:
        root = Path(temp)
        env = profile(root)
        seed(Path(env["LOCALAPPDATA"]) / "Tingsu")
        with ThreadingHTTPServer(("127.0.0.1", 0), SlowModel) as model:
            model.prompts, model.release = [], Event()
            Thread(target=model.serve_forever, daemon=True).start()
            env.update(OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=f"http://127.0.0.1:{model.server_port}/v1")
            try:
                with desktop(app, root, env) as page:
                    check_workspace(page)
                    check_turns(page, model)
                    media_flow(page, root)
            finally:
                model.release.set()
                model.shutdown()
    with TemporaryDirectory(prefix="tingsu-webview2-board-") as temp:
        root = Path(temp)
        env = profile(root)
        with ThreadingHTTPServer(("127.0.0.1", 0), BoardModel) as model:
            model.names, model.meetings, model.unavailable = {}, {k: [] for k in TOPICS}, set()
            Thread(target=model.serve_forever, daemon=True).start()
            env.update(OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=f"http://127.0.0.1:{model.server_port}/v1")
            try:
                board_flow(lambda: desktop(app, root, env), model)
            finally:
                model.shutdown()
