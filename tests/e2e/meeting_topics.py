"""Exercise ten interleaved topic meetings, local history tools, restart and session recovery.

Use --app with either a source launcher or a release. --wine expects an isolated X11
test display. --wayland uses the supplied Wayland compositor.
"""
import argparse
from contextlib import ExitStack, contextmanager
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

from desktop import Page
from openai_fixture import Fixture

TOPICS = ("路线图 <春季>", "渠道复盘 & 预算")
TITLE = "同名例会"


class TopicModel(Fixture):
    """Issue actual Responses tool calls; compare what they return against the UI-created IDs."""

    def do_GET(self):
        if not self.authorized():
            return
        if self.path.startswith("/v1/models/"):
            return self.reply({"id": "test-model"})
        sid = self.path.rsplit("/", 1)[-1]
        if sid in self.server.names and sid not in self.server.unavailable:
            return self.reply({"id": sid, "metadata": {"title": self.server.names[sid]}})
        self.send_error(404)

    def do_POST(self):
        if not self.authorized():
            return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/conversations":
            sid = f"conv_topic_{len(self.server.names) + 1}"
            self.server.names[sid] = req["metadata"]["title"]
            return self.reply({"id": sid, "metadata": req["metadata"]})
        if self.path != "/v1/responses":
            return self.send_error(404)
        sid = req["conversation"]
        topic = self.server.names[sid]
        prior = self.server.meetings[topic][:-1]
        num = len(prior) + 1
        item = req["input"][0]
        if item.get("type") != "function_call_output":
            entry = json.loads(item["content"][0]["text"])
            ctx = json.loads(entry["meeting_context"])
            assert entry["request"] == f"{topic} / {num}"
            assert ctx["主题"] == topic and ctx["本场会议ID"] == self.server.meetings[topic][-1]
            assert [row["id"] for row in ctx["往次会议"]] == prior[::-1]
            return self.tool("list_topic_meetings", "list", {"query": "", "offset": 0, "limit": 20})
        result = json.loads(item["output"])["result"]
        if item["call_id"] == "list":
            assert [row["id"] for row in result["items"]] == prior[::-1]
            if num > 1:
                return self.tool("read_topic_history", "read", {
                    "meeting_id": prior[0], "kind": "chat", "query": " / 1", "offset": 0, "limit": 10
                })
        else:
            assert result["meeting"]["id"] == prior[0]
            assert [row["text"] for row in result["items"] if row["kind"] == "user"] == [f"{topic} / 1"]
        self.message(f"已核对同主题的 {num - 1} 场历史。")


def connect(page, first, choose=False):
    page.click("agent")
    if choose:
        page.field("#connect-provider", "openai")
    else:
        page.until("$('#connect-provider').value==='openai'")
    page.click("connect")
    if first:
        page.until("modal==='agent' && !!$('#dialog [data-action=session-new]') && !$('#session-id')")
        page.click("session-new")
    page.until("S.agent==='ready' && !modal")
    return page.js("S.session")


def scenario(launch, model):
    ids, sessions = {}, {}
    with ExitStack() as processes:
        page = processes.enter_context(launch())
        for num in range(1, 6):
            for i, topic in enumerate(TOPICS):
                prev = page.js("S.meeting.id")
                page.click("new")
                page.field("#meeting-name", f"{TITLE} {num}")
                if num == 1 and i == 1:
                    page.field("#topic-select", "new-topic")
                    page.field("#topic-name", topic)
                elif num > 1:
                    page.field("#topic-select", ids[topic])
                page.click("create")
                page.until("!modal && S.meeting.id!==" + json.dumps(prev))
                if num == 1 and i == 0:
                    # Upgrade a genuine independent meeting. Its name matches the other theme's.
                    assert not page.js("S.meeting.topic_id")
                    page.click("topic")
                    page.field("#topic-select", "new-topic")
                    page.field("#topic-name", topic)
                    page.click("topic-save")
                page.until("!modal && S.meeting.topic_name===" + json.dumps(topic))
                page.until("document.querySelector('.meta-topic span').innerText===" + json.dumps(topic))
                ids[topic] = page.js("S.meeting.topic_id")
                model.meetings[topic].append(page.js("S.meeting.id"))
                session = connect(page, num == 1, choose=(num == 1))
                if num == 1:
                    assert session not in sessions.values()
                    sessions[topic] = session
                assert session == sessions[topic]
                page.field("#message", f"{topic} / {num}")
                page.click("send")
                reply = json.dumps(f"已核对同主题的 {num - 1} 场历史。")
                page.until(f"!S.sending && S.events.some(e=>e.kind==='assistant' && e.text==={reply})")
            print(f"PASS two independent conversations through meeting {num}", flush=True)
            if num == 3:
                processes.close()
                page = processes.enter_context(launch())
                page.until("S.meeting.id===" + json.dumps(model.meetings[TOPICS[1]][-1]))
        assert len(model.names) == 2
        page.click("topic")
        page.until("document.querySelectorAll('.topic-history .topic-row').length===5")
        expected = model.meetings[TOPICS[1]][::-1]
        assert page.js("[...document.querySelectorAll('.topic-row')].map(e=>e.dataset.id)") == expected
        page.click("select", f"{TITLE} 1", area=".topic-history ")
        page.until("!modal && S.meeting.id===" + json.dumps(expected[-1]))
        page.click("topic")
        page.click("topic-new-meeting")
        page.until("modal==='new' && $('#topic-select').value===" + json.dumps(ids[TOPICS[1]]))
        page.click("close")

        # Loss of a remote session must leave all local records and the other binding intact.
        lost = sessions[TOPICS[1]]
        model.unavailable.add(lost)
        page.click("agent")
        page.click("connect")
        page.until("modal==='agent' && !!$('#dialog [data-action=session-replace]')")
        assert page.js("S.meetingSessions.openai") == lost
        page.click("session-replace")
        page.click("confirm-session-replace")
        page.until("S.agent==='ready' && !modal && S.session!==" + json.dumps(lost))
        assert len(model.names) == 3
        page.click("find-meeting")
        page.js(f"$('#meeting-find').value={json.dumps(TOPICS[0])};$('#meeting-find').dispatchEvent(new Event('input',{{bubbles:true}}))")
        page.until("document.querySelectorAll('.recent-item').length===5")
        page.click("select", f"{TITLE} 5", area="#meetings ")
        page.until("S.meeting.id===" + json.dumps(model.meetings[TOPICS[0]][-1]))
        assert connect(page, False) == sessions[TOPICS[0]]
        print("PASS history navigation, topic search, explicit recovery and unaffected other topic", flush=True)


def run(app, wine, wayland):
    with TemporaryDirectory(prefix="tingsu-topics-") as tmp, ExitStack() as cleanup:
        root = Path(tmp)
        model = ThreadingHTTPServer(("127.0.0.1", 0), TopicModel)
        model.names = {}
        model.meetings = {topic: [] for topic in TOPICS}
        model.unavailable = set()
        Thread(target=model.serve_forever, daemon=True).start()
        cleanup.callback(model.server_close)
        cleanup.callback(model.shutdown)
        env = {k: v for k, v in os.environ.items() if not any(x in k for x in ("API_KEY", "AUTH_TOKEN", "OAUTH_TOKEN"))}
        env.update(OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=f"http://127.0.0.1:{model.server_port}/v1")
        if wine:
            from windows_desktop import close_app, windows_path
            env.pop("WAYLAND_DISPLAY", None)
            env.update(WINEPREFIX=str(root / "wine"), WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=",
                       QTWEBENGINE_DISABLE_SANDBOX="1", QT_OPENGL="software",
                       TINGSU_APP_DIR=windows_path(root), QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-first-run")
            cleanup.callback(subprocess.run, ["wineserver", "-w"], env=env, timeout=35)
            cleanup.callback(subprocess.run, ["wineserver", "-k"], env=env, timeout=15)
            subprocess.run(["wineboot", "-u"], env=env, check=True, timeout=120)
            command = ["wine", windows_path(app)]
        else:
            env.update(XDG_DATA_HOME=str(root / "data"), XDG_CACHE_HOME=str(root / "cache"),
                       XDG_CONFIG_HOME=str(root / "config"), TINGSU_APP_DIR=str(root))
            if not wayland:
                env.pop("WAYLAND_DISPLAY", None)
                env.update(QT_NO_GLIB="1", XDG_SESSION_TYPE="x11")
            command = [str(app), "--wayland" if wayland else "--x11"]

        @contextmanager
        def launch():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
            proc = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
            page = None
            try:
                page = Page(port, timeout=120)
                yield page
            finally:
                if wine and page:
                    close_app(proc, page, port)
                else:
                    if page:
                        page.close()
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=15)
        scenario(launch, model)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--wine", action="store_true")
    modes.add_argument("--wayland", action="store_true")
    args = parser.parse_args()
    run(args.app.resolve(), args.wine, args.wayland)
