"""Exercise cancellation and queued messages in the released GUI on a private X11 display."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer

from desktop import Page
from openai_fixture import Fixture


class SlowModel(Fixture):
    def do_GET(self):
        if self.path.startswith("/v1/conversations/conv_acceptance/items"):
            if self.authorized():
                self.reply({"data": []})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/v1/responses":
            return super().do_POST()
        if not self.authorized():
            return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = json.loads(req["input"][0]["content"][0]["text"])["request"]
        self.server.prompts.append(prompt)
        if prompt != "第一条":
            self.message(prompt + "已处理")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"type": "response.output_text.delta", "delta": "正在整理发言"}
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.flush()
        self.server.release.wait(45)


def check(page, server):
    page.click("new")
    page.field("#meeting-name", "回复排队验收")
    page.click("create")
    page.until("S.meeting.title==='回复排队验收'")
    page.click("agent")
    page.field("#connect-provider", "openai")
    page.click("connect")
    page.click("session-new")
    page.until("S.agent==='ready' && S.agentName==='openai'")
    for i, prompt in enumerate(("第一条", "第二条", "第三条")):
        page.field("#message", prompt)
        page.click("send")
        page.until("S.sending && S.queued===" + str(i))
        if i == 0:
            page.until("document.querySelector('#feed-scroll').innerText.includes('正在整理发言')")
    page.click("cancel")
    page.until("!S.sending && S.events.filter(e=>e.kind==='assistant').length===3")
    replies = page.js("S.events.filter(e=>e.kind==='assistant').map(e=>e.text)")
    assert replies == ["正在整理发言\n（回复已停止）", "第二条已处理", "第三条已处理"], replies
    assert server.prompts == ["第一条", "第二条", "第三条"], server.prompts
    assert page.js("S.queued===0 && !S.events.some(e=>e.kind==='system')")
    print("Verified partial reply persistence, cancellation and FIFO delivery of both queued turns.")


def run(app):
    with tempfile.TemporaryDirectory(prefix="tingsu-turns-") as folder, ExitStack() as stack:
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowModel)
        server.prompts, server.release = [], threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stack.callback(server.release.set)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = Path(folder)
        env = dict(os.environ, HOME=folder, XDG_CONFIG_HOME=str(base / "config"),
                   XDG_DATA_HOME=str(base / "data"), XDG_CACHE_HOME=str(base / "cache"), QT_QPA_PLATFORM="xcb",
                   OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
                   CODEX_HOME=str(base / "codex"), QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}")
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.Popen([app, "--x11"], cwd=base, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stack.callback(lambda: proc.wait(10))
        stack.callback(proc.kill)
        page = Page(port)
        stack.callback(page.close)
        check(page, server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    run(parser.parse_args().app.resolve())
