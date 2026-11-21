"""Check the released Windows workspace, keyboard interactions and AI turn queue under Wine."""

import argparse
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import os
from pathlib import Path, PureWindowsPath
import socket
import subprocess
import tempfile
import threading

from desktop import Page
from queued_turns import SlowModel, check as check_turns
from windows_desktop import close_app, windows_path
from workspace_tools import check as check_workspace, seed


def run(app):
    with tempfile.TemporaryDirectory(prefix="tingsu-windows-workspace-") as folder, ExitStack() as stack:
        directory = Path(folder)
        prefix = directory / "wine"
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowModel)
        server.prompts, server.release = [], threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stack.callback(server.release.set)
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY" and not any(
            token in k for token in ("API_KEY", "OAUTH_TOKEN", "AUTH_TOKEN")
        )}
        env.update(
            WINEPREFIX=str(prefix), WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=",
            QTWEBENGINE_DISABLE_SANDBOX="1", QT_OPENGL="software",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-first-run",
            CODEX_HOME=windows_path(directory / "unused-codex"),
            OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        )
        stack.callback(subprocess.run, ["wineserver", "-w"], env=env, timeout=35)
        stack.callback(subprocess.run, ["wineserver", "-k"], env=env, timeout=15)
        subprocess.run(["wineboot", "-u"], env=env, check=True, timeout=120)
        local = PureWindowsPath(subprocess.check_output(
            ["wine", "cmd", "/c", "echo", "%LOCALAPPDATA%"], env=env, text=True
        ).strip())
        if local.drive.lower() != "c:":
            raise RuntimeError("Unexpected profile drive in the disposable Wine prefix")
        seed(prefix.joinpath("drive_c", *local.parts[1:], "Tingsu"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
        proc = subprocess.Popen(["wine", windows_path(app)], cwd=directory, env=env)
        stack.callback(lambda: proc.kill() if proc.poll() is None else None)
        page = Page(port, timeout=120)
        stack.callback(page.close)
        check_workspace(page)
        check_turns(page, server)
        close_app(proc, page, port)
        print("All ten workspace features, cancellation and FIFO replies passed in the Windows GUI.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    run(parser.parse_args().app.resolve())
