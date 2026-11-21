"""Exercise a locally installed agent from the released Linux or Windows single-file app in a dedicated X11 test display."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

from desktop import Page, native_file


def verify_flow(provider, executable, material, launch, close_app, choose_file):
    key = json.dumps(provider)
    ready = "S.agent==='ready' && S.agentName===" + key + " && S.meetingSessions[" + key + "]===S.session"
    proc, page = launch()
    page.click("new")
    page.field("#meeting-name", "本地 CLI 验收")
    page.click("create")
    page.until("S.meeting.title==='本地 CLI 验收'")
    page.click("agent")
    page.field("#connect-provider", provider)
    page.click("ai-settings")
    page.until("document.querySelector('#ai-provider')?.value===" + json.dumps(provider))
    page.field('[data-cli-path="' + provider + '"]', executable)
    page.click("settings-save")
    page.until("S.settings.agent===" + key + " && S.settings[" + json.dumps(provider + "_path") + "]===" + json.dumps(executable))
    page.click("agent")
    page.field("#connect-provider", provider)
    page.click("connect")
    page.until("S.agent==='choose'", 135)
    page.click("session-new")
    page.until(ready, 90)
    session = page.js("S.session")
    page.click("attach")
    choose_file(material)
    page.until("S.files.includes('agenda.txt')")
    previous_replies = page.js("S.events.filter(e=>e.kind==='assistant').length")
    page.field("#message", "阅读附件，以其中的完整编号为正文，创建标题为「CLI 验收」的待办便签，然后确认。")
    page.click("send")
    page.until("S.notes.some(n=>n.body.includes('ORCHID-718'))", 150)
    page.until("!S.sending && S.events.filter(e=>e.kind==='assistant').length>" + str(previous_replies), 120)
    assert page.js("S.notes.filter(n=>n.body.includes('ORCHID-718')).length") == 1
    close_app(proc, page)

    proc, page = launch()
    page.until("S.meeting.title==='本地 CLI 验收' && S.notes.some(n=>n.body.includes('ORCHID-718'))")
    page.click("agent")
    page.field("#connect-provider", provider)
    page.click("connect")
    page.until("S.agent==='choose'", 135)
    page.click("session", "继续本会议的会话")
    page.until(ready + " && S.session===" + json.dumps(session), 90)
    close_app(proc, page)
    print(json.dumps({"provider": provider, "real_cli": True, "attachment_note": True, "resume": True}))


def verify(app, provider, executable, directory, deepseek_fixture=False, *, wine=False, claude_fixture=False):
    native_path = str
    if wine:
        from windows_desktop import choose_windows_file, close_app as close_windows, windows_path
        native_path = windows_path

    env = os.environ.copy()
    env["CODEX_HOME"] = str(directory / "unused-codex-profile")
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        path = directory / variable
        path.mkdir(mode=0o700)
        env[variable] = str(path)
    env.update(XDG_SESSION_TYPE="x11", QT_NO_GLIB="1", QT_QPA_PLATFORMTHEME="generic")
    env.pop("WAYLAND_DISPLAY", None)
    process_ports = {}
    prior_ports = set()
    material = directory / "agenda.txt"
    material.write_text("本次验收材料的编号是 ORCHID-718。", encoding="utf-8")

    with ExitStack() as cleanup:
        if wine:
            env.update(WINEPREFIX=str(directory / "wine"), WINEDEBUG="-all",
                       WINEDLLOVERRIDES="mscoree,mshtml=", QTWEBENGINE_DISABLE_SANDBOX="1",
                       QT_OPENGL="software", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-first-run")
            clean_env = {k: v for k, v in env.items() if not any(
                marker in k for marker in ("API_KEY", "AUTH_TOKEN", "OAUTH_TOKEN")
            )}
            cleanup.callback(subprocess.run, ["wineserver", "-w"], env=clean_env, timeout=35)
            cleanup.callback(subprocess.run, ["wineserver", "-k"], env=clean_env, timeout=15)
            subprocess.run(["wineboot", "-u"], env=clean_env, check=True, timeout=120)
        if deepseek_fixture or claude_fixture:
            from deepseek_fixture import TOKEN, start
            fixture = start(tool_prefix="mcp__meeting__" if claude_fixture else "mcp__tingsu__")
            cleanup.callback(fixture.server_close)
            cleanup.callback(fixture.shutdown)
            url = f"http://127.0.0.1:{fixture.server_port}"
            if deepseek_fixture:
                env.update(DSH_HOME=native_path(directory / "dsh"), DEEPSEEK_API_KEY=TOKEN, DEEPSEEK_BASE_URL=url)
            else:
                for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
                    env.pop(name, None)
                env.update(CLAUDE_CONFIG_DIR=native_path(directory / "claude"), ANTHROPIC_API_KEY=TOKEN,
                           ANTHROPIC_BASE_URL=url, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")

        def launch():
            while True:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                if port not in prior_ports:
                    break
            prior_ports.add(port)
            env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
            command = ["wine", native_path(app)] if wine else [str(app), "--x11"]
            proc = subprocess.Popen(command, cwd=directory, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cleanup.callback(lambda: proc.kill() if proc.poll() is None else None)
            process_ports[proc] = port
            page = Page(port, timeout=120)
            cleanup.callback(page.close)
            return proc, page

        def close_app(proc, page):
            if wine:
                return close_windows(proc, page, process_ports.pop(proc))
            window = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", "--name", "^听序 · 会议工作台$"], text=True
            ).splitlines()[-1]
            subprocess.run(["xdotool", "windowactivate", "--sync", window, "key", "alt+F4"], check=True)
            proc.wait(timeout=25)
            page.close()

        verify_flow(provider, native_path(executable), material, launch, close_app,
                    choose_windows_file if wine else native_file)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, required=True)
    cli.add_argument("--provider", choices=["claude", "deepseek"], required=True)
    cli.add_argument("--executable", type=Path, required=True)
    cli.add_argument("--deepseek-fixture", action="store_true",
                     help="Use the real local dsh against a synthetic loopback model.")
    cli.add_argument("--wine", action="store_true", help="Launch Windows EXE and Windows CLI in an isolated Wine prefix.")
    cli.add_argument("--claude-fixture", action="store_true", help="Point real Claude Code at a synthetic Anthropic-compatible endpoint.")
    args = cli.parse_args()
    if args.claude_fixture and args.provider != "claude":
        cli.error("--claude-fixture applies only to Claude Code")
    if args.deepseek_fixture and args.provider != "deepseek":
        cli.error("--deepseek-fixture applies only to the DeepSeek Harness CLI")
    with tempfile.TemporaryDirectory(prefix="tingsu-local-agent-") as folder:
        verify(args.app.absolute(), args.provider, args.executable.absolute(), Path(folder), args.deepseek_fixture,
               wine=args.wine, claude_fixture=args.claude_fixture)
