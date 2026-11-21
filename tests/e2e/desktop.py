"""Drive a released Linux GUI in a disposable X11 desktop. See docs/acceptance.md."""

import argparse
import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket
from openai_fixture import start as start_fixture


def wait(fn, label, timeout=35):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if value := fn():
                return value
        except (ConnectionError, OSError, ValueError):
            pass
        time.sleep(0.15)
    raise TimeoutError(label)


class Page:
    def __init__(self, port, timeout=90):
        def attach():
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                pages = json.load(response)
            return next(
                (
                    x["webSocketDebuggerUrl"]
                    for x in pages
                    if x.get("type") == "page" and x.get("url", "").endswith("/index.html")
                ),
                None,
            )

        url = wait(attach, "QtWebEngine page", timeout)
        # A host port forward can differ from the debugger address inside a test VM.
        url = f"ws://127.0.0.1:{port}/" + url.split("/", 3)[-1]
        self.ws = websocket.create_connection(url, suppress_origin=True, timeout=10)
        self.seq = 0
        self.until("document.getElementById('title')?.textContent", timeout)

    def js(self, code):
        self.seq += 1
        self.ws.send(
            json.dumps(
                {
                    "id": self.seq,
                    "method": "Runtime.evaluate",
                    "params": {"expression": code, "returnByValue": True},
                }
            )
        )
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    raise RuntimeError(result["exceptionDetails"].get("text", "JS exception"))
                return result.get("result", {}).get("value")

    def until(self, code, timeout=35):
        try:
            return wait(lambda: self.js(code), "UI condition", timeout)
        except TimeoutError:
            notices = self.js("document.getElementById('notices').innerText")
            raise AssertionError(f"UI timeout: {code}; notices={notices}") from None

    def click(self, action, match="", area=""):
        selector = json.dumps(area + '[data-action="' + action + '"]')
        expr = f"Array.from(document.querySelectorAll({selector})).find(e=>e.getClientRects().length && !e.disabled && e.innerText.includes({json.dumps(match)}))"
        self.until("!!" + expr)
        print("GUI", action, flush=True)
        self.js("setTimeout(()=>"+expr+".click(),0);true")

    def field(self, selector, value):
        el = "document.querySelector(" + json.dumps(selector) + ")"
        self.until("!!" + el)
        self.js(
            f'{el}.value={json.dumps(value)};{el}.dispatchEvent(new Event("change",{{bubbles:true}}))'
        )

    def close(self):
        self.ws.close()


def make_fixtures(directory):
    from PIL import Image, ImageDraw, ImageFont

    directory.mkdir(exist_ok=True, parents=True)
    image = Image.new("RGB", (640, 360), "#0e523e")
    font = ImageFont.load_default(size=46)
    ImageDraw.Draw(image).text((68, 135), "SAGE 417", font=font, fill="white")
    image.save(directory / "slide.png")
    (directory / "agenda.txt").write_text("Synthetic agenda. " * 10_000 + "Delivery verification code: ORCHID-718.", encoding="utf-8")
    from reportlab.pdfgen.canvas import Canvas
    pdf=Canvas(str(directory/"agenda.pdf"))
    pdf.drawString(70,740,"Delivery verification code: ORCHID-718.")
    pdf.save()
    subprocess.run(
        [
            "espeak",
            "-s",
            "145",
            "-w",
            str(directory / "speech.wav"),
            "The product review is on Friday morning. Jordan will bring the slides. Our meeting starts at ten.",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "6",
            "-i",
            str(directory / "slide.png"),
            "-i",
            str(directory / "speech.wav"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(directory / "QA meeting.mp4"),
        ],
        check=True,
    )


def native_file(path):
    # Only use in the dedicated test display created by the caller.
    def dialog():
        cmd = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"], capture_output=True, text=True
        )
        return cmd.returncode == 0 and any(
            s in cmd.stdout for s in ("添加会议", "添加媒体", "导出会议")
        )

    wait(dialog, "native file chooser", 12)
    subprocess.run(["xdotool", "key", "ctrl+l"], check=True)
    time.sleep(0.14)
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "2", str(path)], check=True)
    subprocess.run(["xdotool", "key", "Return"], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--provider", choices=["siliconflow", "sophnet"], default="siliconflow")
    parser.add_argument("--window", action="store_true", help="Select and recapture a real X11 test window.")
    parser.add_argument("--agent", choices=["fixture", "codex"], default="fixture")
    parser.add_argument(
        "--retry", action="store_true", help="First use an invalid ASR key and recover the queued WAV."
    )
    args = parser.parse_args()
    key = os.getenv(args.provider.upper() + "_API_KEY") or ""
    if not key:
        raise SystemExit("Provide the live ASR key via its environment variable.")
    executable = args.app.resolve()
    sample_title = "完整验收会议"
    with tempfile.TemporaryDirectory(prefix="tingsu-e2e-") as name, contextlib.ExitStack() as cleanup:
        base = Path(name)
        media = base / "fixtures"
        make_fixtures(media)
        config = base / "config"
        data = base / "data"
        home = base / "home"
        home.mkdir()
        env = {
            **os.environ,
            "XDG_DATA_HOME": str(data),
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(base / "cache"),
            "HOME": str(home),
            "QT_QPA_PLATFORM": "xcb",
            "XDG_SESSION_TYPE": "x11",
            "QT_NO_GLIB": "1",
        }
        for k in (
            "SILICONFLOW_API_KEY",
            "SOPHNET_API_KEY",
            "OPENAI_API_KEY",
            "WAYLAND_DISPLAY",
            "OPENAI_BASE_URL",
        ):
            env.pop(k, None)
        if args.agent == "fixture":
            fixture = start_fixture()
            cleanup.callback(fixture.server_close)
            cleanup.callback(fixture.shutdown)
            env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{fixture.server_port}/v1"
            env["OPENAI_API_KEY"] = "tingsu-local-acceptance"
        else:
            original = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
            codex_home = base / "codex"
            codex_home.mkdir(mode=0o700)
            (codex_home / "auth.json").symlink_to(original)
            env["CODEX_HOME"] = str(codex_home)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
        log = cleanup.enter_context((base / "gui.log").open("w"))

        def launch():
            proc = subprocess.Popen(
                [str(executable), "--x11"], env=env, cwd=base, stdout=log, stderr=log
            )
            cleanup.callback(lambda: proc.kill() if proc.poll() is None else None)
            page = Page(port)
            cleanup.callback(page.close)
            return proc, page

        def close_app(proc, page):
            w = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", "--name", "^听序 · 会议工作台$"], text=True
            ).splitlines()[-1]
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", w, "key", "alt+F4"], check=True, timeout=8
            )
            proc.wait(timeout=20)
            page.close()

        proc, p = launch()
        p.click("settings")
        p.field("#provider", args.provider)
        p.field("#asr-key", "invalid-acceptance-key" if args.retry else key)
        p.click("settings-save")
        p.click("library")
        p.click("import")
        native_file(media / "QA meeting.mp4")
        p.click("media", "QA meeting")
        p.field("#meeting-name", sample_title)
        p.click("create")
        p.until("document.getElementById('title').innerText==='完整验收会议'")
        p.click("agent")
        p.field("#connect-provider", "openai" if args.agent == "fixture" else "codex")
        p.click("connect")
        p.click("session-new")
        p.until("document.getElementById('agent-label').innerText.includes('已连接')", 90)
        p.click("start")
        if args.retry:
            p.until("!!document.querySelector('#backlog [data-action=retry]')", 80)
            p.click("settings")
            p.field("#asr-key", key)
            p.click("settings-save")
            p.click("retry")
        p.until(
            "document.querySelector('#feed-list .event-speech .event-copy')?.textContent.length>0", 100
        )
        p.until("!!document.querySelector('#feed-list .event-ai')", 150)
        p.click("screen")
        p.click("snapshot")
        p.until("document.querySelector('.screen-image')?.naturalWidth>0")
        p.click("frame-attach")
        p.click("attach")
        attachment = "agenda.pdf" if args.agent=="codex" else "agenda.txt"
        native_file(media / attachment)
        p.until("document.querySelectorAll('#file-list .chip').length===2")
        p.field(
            "#message",
            "请读取附件中的 ORCHID 交付码、截图和会议转写，创建一张标题严格为“验收行动项”的待办，正文写入附件里的完整交付码，然后回复。",
        )
        p.click("send")
        p.until("document.querySelector('#notes').innerText.includes('ORCHID-718')", 150)
        p.until("document.querySelectorAll('#feed-list .event-ai').length>=2", 120)
        p.click("note-edit", "验收行动项")
        p.field("#note-body", "已核对 ORCHID-718")
        p.click("note-save")
        p.click("note-new")
        p.field("#note-title", "临时便签")
        p.click("note-save")
        p.click("note-edit", "临时便签")
        p.click("note-delete")
        p.click("note-confirm-delete")
        p.until("!document.querySelector('#notes').innerText.includes('临时便签')")
        if args.window:
            from x11_window import verify_window
            verify_window(p,data,base)
        p.click("export")
        native_file(base / "report.md")
        wait(lambda: (base / "report.md").exists(), "Markdown export")
        exported = (base / "report.md").read_text(encoding="utf-8")
        assert sample_title in exported and "ORCHID-718" in exported and attachment in exported
        close_app(proc, p)
        proc, p = launch()
        p.until(
            "document.querySelector('#title').innerText==='完整验收会议' && document.querySelector('#notes').innerText.includes('已核对')"
        )
        p.click("agent")
        p.field("#connect-provider", "openai" if args.agent == "fixture" else "codex")
        p.click("connect")
        p.click("session", "继续本会议的会话")
        p.until("document.querySelector('#agent-label').innerText.includes('已连接')", 90)
        close_app(proc, p)
        db = sqlite3.connect(data / "tingsu" / "studio.sqlite3")
        mid = db.execute("SELECT id FROM meetings WHERE title=?", (sample_title,)).fetchone()[0]
        stats = dict(
            db.execute("SELECT kind,count(*) FROM events WHERE meeting=? GROUP BY kind", (mid,))
        )
        assert stats["speech"] and stats["user"] == 1 and stats["assistant"] >= 2
        assert not db.execute("SELECT count(*) FROM jobs WHERE meeting=?", (mid,)).fetchone()[0]
        db.close()
        print(
            json.dumps(
                {
                    "provider": args.provider,
                    "agent": args.agent,
                    "retry": args.retry,
                    "events": stats,
                    "export": True,
                    "restart": True,
                    "resume": True,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
