"""Desktop events and native dialogs over a private pipe to the platform webview."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


class DesktopHost:
    def __init__(self, process, loop):
        self.process = process
        self.loop = loop
        self.pending = {}
        self.serial = 0
        self.closing = False
        self.bridge = None

    def send(self, packet):
        try:
            self.process.stdin.write(json.dumps(packet, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.loop.stop()

    def signal(self, name, *args):
        self.send({"type": "signal", "name": name, "args": args})

    def post(self, fn):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(fn)

    def later(self, seconds, fn):
        return self.loop.call_later(seconds, fn)

    def dialog(self, kind, title, done, *, path="", filter="", message=""):
        self.serial += 1
        self.pending[self.serial] = done
        self.send(dict(type="dialog", id=self.serial, kind=kind, title=title,
                       path=path, filter=filter, message=message))

    def quit(self):
        self.send({"type": "quit"})
        self.loop.stop()

    def receive(self, packet):
        kind = packet.get("type")
        if kind == "dialog-result":
            done = self.pending.pop(packet.get("id"), None)
            if done:
                done(packet.get("value"))
        elif kind == "ready":
            if self.bridge is None:
                self.dialog("message", "听序", lambda _: self.quit(), message="同一个本地工作空间已经打开。")
            else:
                self.bridge.ready()
        elif kind == "command" and self.bridge is not None:
            self.bridge.command(packet.get("name", ""), packet.get("args", {}))
        elif kind == "close-request" and not self.closing:
            if self.bridge is None or not self.bridge.has_active_work:
                self.quit()
                return
            self.closing = True
            def confirm(yes):
                self.closing = False
                if yes is True:
                    self.quit()
            self.dialog("confirm", "退出听序", confirm,
                        message="退出会结束当前采集和 AI 回复。已录制的待转写分片可在下次打开时重试。是否退出？")

    def run(self):
        def dispatch(packet):
            try:
                self.receive(packet)
            except Exception as exc:
                if self.bridge:
                    self.bridge.fail(exc)
                else:
                    self.quit()

        def read():
            try:
                for line in self.process.stdout:
                    try:
                        packet = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(packet, dict):
                        self.post(lambda p=packet: dispatch(p))
            finally:
                self.post(self.loop.stop)
        thread = threading.Thread(target=read, name="desktop-events", daemon=True)
        thread.start()
        try:
            self.loop.run_forever()
        finally:
            if self.bridge:
                self.bridge.shutdown()
            self.process.stdin.close()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            thread.join(timeout=1)
            self.process.stdout.close()
        return self.process.returncode


def start_shell():
    ui = Path(__file__).resolve().parent / "ui/index.html"
    name = "tingsu-webview.exe" if os.name == "nt" else "tingsu-webview"
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    host = os.environ.get("TINGSU_DESKTOP_HOST")
    if not host:
        candidates = (bundle / "bin" / name, bundle / name,
                      ui.parents[1] / "native" / name, ui.parents[2] / "build" / name)
        host = next((str(p) for p in candidates if p.is_file()), name)
    env = os.environ.copy()
    if sys.platform.startswith("linux"):
        if "--x11" in sys.argv:
            env.update(GDK_BACKEND="x11", TINGSU_CAPTURE_DESKTOP="x11")
        elif "--wayland" in sys.argv:
            env.update(GDK_BACKEND="wayland", TINGSU_CAPTURE_DESKTOP="wayland")
        elif env.get("WAYLAND_DISPLAY"):
            env.setdefault("GDK_BACKEND", "wayland,x11")
        if env.get("TINGSU_CAPTURE_DESKTOP"):
            os.environ["TINGSU_CAPTURE_DESKTOP"] = env["TINGSU_CAPTURE_DESKTOP"]
    return subprocess.Popen([host, str(ui)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1, env=env)
