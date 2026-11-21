"""Run the released Windows single-file application in a disposable Wine desktop."""

import argparse
import base64
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request

from PIL import Image, ImageGrab

from desktop import Page, wait
from openai_fixture import start as start_fixture


def windows_path(path):
    # A newly initialized Wine prefix maps Z: to the host filesystem root.
    return "Z:" + str(path.resolve()).replace("/", "\\")


def synthetic_meeting(directory):
    media = directory / "videos"
    media.mkdir()
    speech = directory / "speech.wav"
    subprocess.run(
        [
            "espeak", "-s", "140", "-w", str(speech),
            "The product review is on Friday morning. Jordan will bring the slides. "
            "The project meeting starts at ten.",
        ],
        check=True,
    )
    width, height = 320, 180
    first = bytes([240, 20, 20]) * (width * height)
    last = bytes([20, 20, 240]) * (width * height)
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{width}x{height}", "-framerate", "1", "-i", "pipe:0",
            "-i", str(speech), "-c:v", "mpeg4", "-g", "12", "-sc_threshold", "1000000000",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(media / "portable-test.mp4"),
        ],
        input=first + last,
        check=True,
    )
    (directory / "agenda.txt").write_text(
        "Synthetic agenda. " * 10_000 + "Delivery verification code: ORCHID-718.", encoding="utf-8"
    )


def visible_window(title):
    result = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", title],
        capture_output=True, text=True,
    )
    return result.stdout.splitlines()[-1] if result.stdout.strip() else None


def choose_windows_file(path):
    window = wait(lambda: visible_window("添加会议材料"), "native Windows file picker", 35)
    subprocess.run(["xdotool", "windowfocus", window, "key", "alt+n"], check=True)
    subprocess.run(["xdotool", "type", "--clearmodifiers", windows_path(path)], check=True)
    subprocess.run(["xdotool", "key", "Return"], check=True)


def close_app(proc, page, port):
    window = wait(lambda: visible_window("会议工作台"), "Windows application window", 10)
    subprocess.run(["xdotool", "windowfocus", window, "key", "alt+F4"], check=True)
    page.close()
    proc.wait(timeout=30)

    def debugger_closed():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.5):
                return False
        except OSError:
            return True

    wait(debugger_closed, "closed GUI debugger endpoint", 15)


def verify(app, screenshot, directory, cleanup, *, retry=False, provider="siliconflow"):
    synthetic_meeting(directory)
    fixture = start_fixture()
    cleanup.callback(fixture.server_close)
    cleanup.callback(fixture.shutdown)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = {
        **os.environ,
        "WINEPREFIX": str(directory / "wine"),
        "WINEDEBUG": "-all",
        "WINEDLLOVERRIDES": "mscoree,mshtml=",
        "QTWEBENGINE_DISABLE_SANDBOX": "1",
        "QT_OPENGL": "software",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu --no-first-run",
        "QTWEBENGINE_REMOTE_DEBUGGING": f"127.0.0.1:{port}",
        "TINGSU_APP_DIR": windows_path(directory),
        "OPENAI_API_KEY": "tingsu-local-acceptance",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{fixture.server_port}/v1",
    }
    env.pop("WAYLAND_DISPLAY", None)
    # Supply the ASR credential through the real GUI in the disposable test profile.
    env.pop("SOPHNET_API_KEY", None)
    env.pop("SILICONFLOW_API_KEY", None)
    cleanup.callback(subprocess.run, ["wineserver", "-w"], env=env, timeout=35)
    cleanup.callback(subprocess.run, ["wineserver", "-k"], env=env, timeout=15)
    subprocess.run(["wineboot", "-u"], env=env, check=True, timeout=120)

    def launch():
        proc = subprocess.Popen(["wine", windows_path(app)], cwd=directory, env=env)
        cleanup.callback(lambda: proc.kill() if proc.poll() is None else None)
        page = Page(port, timeout=120)
        cleanup.callback(page.close)
        page.until("typeof S==='object' && S?.meeting?.id")
        return proc, page

    def configure(page, key):
        page.click("settings")
        page.field("#provider", provider)
        page.field("#asr-key", key)
        page.click("settings-save")
        page.until("!modal && S.settings.provider===" + json.dumps(provider))

    live_asr_key = os.environ[provider.upper() + "_API_KEY"]
    proc, page = launch()
    configure(page, "invalid-transcription-recovery-test" if retry else live_asr_key)
    page.click("library")
    page.click("media", "portable-test")
    page.field("#meeting-name", "Portable end-to-end")
    page.click("create-start")
    page.until("S.capture==='recording'")
    if retry:
        page.until("S.capture==='idle' && S.pending>0 && !S.events.some(e=>e.kind==='speech')", 120)
        pending = page.js("S.pending")
        mid = page.js("S.meeting.id")
        close_app(proc, page, port)
        proc, page = launch()
        assert page.js("S.meeting.id") == mid and page.js("S.pending") == pending
        configure(page, live_asr_key)
        page.click("retry")
    page.until("S.capture==='idle' && S.events.some(e=>e.kind==='speech')", 150)
    assert page.js("S.pending") == 0
    speech_events = page.js("S.events.filter(e=>e.kind==='speech' && e.text.trim()).length")
    duration = page.js("S.meeting.duration")
    assert speech_events and duration > 6, (speech_events, duration)
    page.click("screen")
    page.click("snapshot")
    frame_url = page.until("document.querySelector('.screen-image')?.src")
    with Image.open(io.BytesIO(base64.b64decode(frame_url.split(",", 1)[1]))) as frame:
        r, g, b = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
    assert b > 180 and r < 80 and g < 80, f"Expected the blue final frame, got {(r, g, b)}"
    page.click("frame-attach")
    page.click("agent")
    page.field("#connect-provider", "openai")
    page.click("connect")
    page.click("session-new")
    page.until("S.agent==='ready'")
    page.click("attach")
    choose_windows_file(directory / "agenda.txt")
    page.until("S.files.includes('agenda.txt')")
    page.field("#message", "Read the ORCHID code, slide and transcript, then make a task.")
    page.click("send")
    # The local Responses server only approves the note after verifying both attachments,
    # the authenticated conversation ID, and a read_transcript tool result with real speech.
    page.until("S.notes.some(n=>n.body.includes('ORCHID-718'))")
    page.until("S.events.some(e=>e.text.includes('已读取转写和材料并保存便签'))")
    meeting_id = page.js("S.meeting.id")
    if screenshot:
        ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(screenshot)
    close_app(proc, page, port)

    proc, page = launch()
    assert page.js("S.meeting.id") == meeting_id
    assert page.js(
        "S.notes.some(n=>n.body.includes('ORCHID-718')) && S.events.some(e=>e.kind==='speech')"
        " && S.meeting.session==='conv_acceptance'"
    )
    page.click("agent")
    page.field("#connect-provider", "openai")
    page.click("connect")
    page.click("session", "继续本会议的会话")
    page.until("S.agent==='ready' && S.session==='conv_acceptance'")
    close_app(proc, page, port)
    print(json.dumps({
        "provider": provider, "speech_segments": speech_events, "seconds": duration,
        "final_frame_rgb": [r, g, b], "native_file_picker": True,
        "agent_fixture_note": True, "restored_meeting_and_session": True, "retried_after_restart": retry,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path, help="Released Windows x64 EXE")
    parser.add_argument("--provider", choices=["siliconflow", "sophnet"], default="siliconflow")
    parser.add_argument("--screenshot", type=Path, help="Save a screenshot of the tested desktop")
    parser.add_argument("--retry", action="store_true",
                        help="Fail transcription, restart the EXE and recover its durable queue from the GUI.")
    args = parser.parse_args()
    if not args.app.is_file():
        parser.error("Pass the path to the Windows release EXE.")
    if not os.environ.get(args.provider.upper() + "_API_KEY"):
        parser.error("Set the selected provider's API key in the environment for the live ASR test.")
    with tempfile.TemporaryDirectory(prefix="tingsu-windows-e2e-") as name, ExitStack() as cleanup:
        verify(args.app.resolve(), args.screenshot, Path(name), cleanup, retry=args.retry, provider=args.provider)
