"""Run the Windows release GUI and a separately installed official Codex against a loopback model."""

import argparse
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread

from desktop import Page
from external_codex import CodexFixture


@contextmanager
def desktop(app, root, env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([str(app)], env={**env, "QTWEBENGINE_REMOTE_DEBUGGING": f"127.0.0.1:{port}"},
                            cwd=root)
    page = None
    try:
        page = Page(port, timeout=120)
        page.until("S?.meeting?.id")
        yield page
    finally:
        if page:
            page.close()
        if proc.poll() is None:
            # A PyInstaller one-file launcher owns a second executable process containing the Qt window.
            # Select only the main window from the verified executable path on this disposable runner.
            close = """
            $name = [IO.Path]::GetFileNameWithoutExtension($env:QA_APP)
            $windows = @(Get-Process -Name $name | Where-Object { $_.Path -eq $env:QA_APP -and $_.MainWindowHandle -ne 0 })
            if ($windows.Count -ne 1 -or !$windows[0].CloseMainWindow()) { exit 1 }
            """
            try:
                subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", close], check=True,
                               env={**env, "QA_APP": str(app)}, timeout=20)
                assert proc.wait(timeout=35) == 0, "Desktop did not exit cleanly"
            finally:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/pid", str(proc.pid), "/t", "/f"], timeout=20)
                    proc.wait(timeout=15)


def verify(app, codex):
    if sys.platform != "win32":
        raise RuntimeError("A native Windows runner is required")
    with tempfile.TemporaryDirectory(prefix="tingsu-release-qa-", dir=Path.home()) as profile:
        root = Path(profile)
        home = root / "codex-profile"
        home.mkdir()
        # Run with an empty desktop profile. All model inputs, credentials and replies are synthetic.
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("TOKEN", "API_KEY"))}
        for key, folder in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("USERPROFILE", "user")):
            path = root / folder
            path.mkdir()
            env[key] = str(path)
        env.update(CODEX_HOME=str(home), HOME=env["USERPROFILE"], TINGSU_APP_DIR=str(root),
                   QT_OPENGL="software", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-first-run")
        with ThreadingHTTPServer(("127.0.0.1", 0), CodexFixture) as model:
            model.received = []
            Thread(target=model.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{model.server_port}/v1"
                env.update(OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=base)
                (home / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": env["OPENAI_API_KEY"]}),
                                                encoding="utf-8")
                (home / "config.toml").write_text(
                    'model = "qa-windows"\nmodel_provider = "fixture"\n[model_providers.fixture]\n'
                    f'name = "Loopback"\nbase_url = "{base}"\nwire_api = "responses"\nsupports_websockets = false\n',
                    encoding="utf-8")
                with desktop(app, root, env) as page:
                    page.click("agent")
                    page.field("#connect-provider", "codex")
                    page.click("ai-settings")
                    page.field('[data-cli-path="codex"]', str(codex))
                    page.click("settings-save")
                    page.until("!modal && S.settings.codex_path===" + json.dumps(str(codex)))
                    page.click("agent")
                    page.click("connect")
                    page.until("S.agent==='choose'", 90)
                    page.click("session-new")
                    page.until("S.agent==='ready' && !modal", 90)
                    session = page.js("S.session")
                    assert session, "Missing Codex thread ID"
                    page.field("#message", "ORCHID-718")
                    page.click("send")
                    reply = "e.kind==='assistant' && e.text==='ORCHID-718 received'"
                    page.until(f"!S.sending && S.events.some(e=>{reply})", 100)
                with desktop(app, root, env) as page:
                    page.until(f"S.events.some(e=>{reply})")
                    page.click("agent")
                    page.click("connect")
                    page.until("S.agent==='choose'", 90)
                    page.click("session", "继续本会议的会话")
                    page.until("S.agent==='ready' && S.session===" + json.dumps(session), 90)
                    page.field("#message", "ORCHID-718 again")
                    page.click("send")
                    page.until(f"!S.sending && S.events.filter(e=>{reply}).length===2", 100)
                assert len(model.received) == 2, model.received
                print("PASS native Windows release, external Codex, two turns on loopback, restart and same thread")
            finally:
                model.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--codex", required=True, type=Path)
    args = parser.parse_args()
    verify(args.app.resolve(strict=True), args.codex.resolve(strict=True))
