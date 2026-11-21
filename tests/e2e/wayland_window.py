"""Capture a real Wayland window through the released GUI on a private labwc desktop.

Requires a host with labwc, xdg-desktop-portal-wlr >= 0.8, PipeWire, WirePlumber,
PySide6, Pillow and websocket-client. Run:
python tests/e2e/wayland_window.py --app dist/Tingsu-Linux-x86_64.AppImage
"""

import argparse
import contextlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TITLE = "Synthetic Wayland meeting"
SIZE = (640, 460)


def slide(folder):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication([])
    window = QWidget(None, Qt.WindowType.FramelessWindowHint)
    window.setWindowTitle(TITLE)
    window.setFixedSize(*SIZE)
    if (folder / "vision-code").is_file():
        label = QLabel((folder / "vision-code").read_text(), window)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setGeometry(32, 168, SIZE[0] - 64, 110)
        label.setStyleSheet("font: bold 50px sans-serif; color: white; background: transparent")
    previous = None

    def repaint():
        nonlocal previous
        color = (folder / "color").read_text()
        if color != previous:
            window.setStyleSheet("background-color:" + color)
            previous = color

    repaint()
    window.show()
    ticker = QTimer(window)
    ticker.timeout.connect(repaint)
    ticker.start(60)
    QTimer.singleShot(250, lambda: (folder / "slide-ready").touch())
    return app.exec()


def chooser(folder):
    """A dmenu-compatible Qt picker driven by real widget clicks on the private seat."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QListWidget,
        QVBoxLayout,
    )

    rows = sys.stdin.read().splitlines()
    app = QApplication([])
    dialog = QDialog()
    dialog.setWindowTitle("Choose a shared window")
    layout = QVBoxLayout(dialog)
    sources = QListWidget(dialog)
    sources.addItems(rows)
    layout.addWidget(sources)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=dialog)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    target = next((i for i in range(len(rows)) if TITLE in rows[i]), None)
    if target is None:
        raise RuntimeError(f"Portal did not offer synthetic window: {rows}")
    entry = sources.item(target)

    def select():
        sources.scrollToItem(entry)
        QTest.mouseClick(
            sources.viewport(),
            Qt.MouseButton.LeftButton,
            pos=sources.visualItemRect(entry).center(),
        )
        QTest.mouseClick(
            buttons.button(QDialogButtonBox.StandardButton.Ok),
            Qt.MouseButton.LeftButton,
        )

    QTimer.singleShot(300, select)
    if dialog.exec() != QDialog.DialogCode.Accepted or sources.currentItem() != entry:
        return 1
    (folder / "selection").write_text(entry.text())
    print(entry.text(), flush=True)
    return 0


def service_binary(name):
    for directory in ("/usr/lib", "/usr/libexec", "/usr/lib/xdg-desktop-portal"):
        path = Path(directory) / name
        if path.is_file():
            return path
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(name)


def verify(app_path, labwc, folder, claude_vision=False, meeting=False, workspace_tools=False, worker_crash=False):
    from PIL import Image
    from desktop import debug_environment, Page, make_fixtures, wait

    env = os.environ.copy()
    for name in (
        "DISPLAY",
        "WAYLAND_SOCKET",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "PIPEWIRE_REMOTE",
        "PIPEWIRE_RUNTIME_DIR",
        "PULSE_SERVER",
        "PULSE_RUNTIME_PATH",
        "KDE_FULL_SESSION",
        "SESSION_MANAGER",
        "SILICONFLOW_API_KEY",
        "SOPHNET_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORMTHEME",
    ):
        env.pop(name, None)
    for name in (
        "HOME",
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
    ):
        path = folder / name.lower()
        path.mkdir(mode=0o700)
        env[name] = str(path)
    env.update(
        XDG_CURRENT_DESKTOP="wlr",
        XDG_SESSION_TYPE="wayland",
        WAYLAND_DISPLAY="wayland-0",
        QT_QPA_PLATFORM="wayland",
        GIO_USE_VFS="local",
        LANG="C.UTF-8",
        LC_ALL="C.UTF-8",
        WLR_BACKENDS="headless",
        WLR_HEADLESS_OUTPUTS="1",
        WLR_RENDERER="pixman",
        WLR_LIBINPUT_NO_DEVICES="1",
        LIBGL_ALWAYS_SOFTWARE="1",
    )
    if claude_vision:
        import secrets
        host_config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        config_copy = folder / "claude-profile"
        config_copy.mkdir(mode=0o700)
        for name in ("settings.json", ".credentials.json"):
            if (host_config / name).is_file():
                (config_copy / name).symlink_to(host_config / name)
        env["CLAUDE_CONFIG_DIR"] = str(config_copy)
        challenge = "MEET-" + str(10000 + secrets.randbelow(90000))
        (folder / "vision-code").write_text(challenge)
    if meeting:
        release = folder / "release"
        release.mkdir()
        make_fixtures(release / "videos")
        # Launch the delivered executable from a directory containing only our synthetic media.
        shipped = release / app_path.name
        try:
            os.link(app_path, shipped)
        except OSError:
            shutil.copy2(app_path, shipped)
        app_path = shipped
    if workspace_tools:
        from workspace_tools import seed
        seeded = folder / "workspace-fixture"
        seed(seeded)
        for area in ("data", "config"):
            (seeded / area).rename(Path(env["XDG_" + area.upper() + "_HOME"]) / "tingsu")
    script = str(Path(__file__).resolve())
    config = Path(env["XDG_CONFIG_HOME"])
    runtime = Path(env["XDG_RUNTIME_DIR"])
    for name in ("xdg-desktop-portal", "xdg-desktop-portal-wlr"):
        (config / name).mkdir()
    (config / "xdg-desktop-portal/portals.conf").write_text(
        "[preferred]\ndefault=none\norg.freedesktop.impl.portal.ScreenCast=wlr\n"
    )
    command = shlex.join([sys.executable, script, "--chooser", str(folder)])
    (config / "xdg-desktop-portal-wlr/config").write_text(
        f"[screencast]\nchooser_type=dmenu\nchooser_cmd={command}\nmax_fps=8\n"
    )
    data = Path(env["XDG_DATA_HOME"]) / "tingsu" / "frames"
    with contextlib.ExitStack() as cleanup:

        def start(name, argv, extra=None, stdout=None):
            log = cleanup.enter_context((folder / (name + ".log")).open("w"))
            proc = subprocess.Popen(
                argv,
                env={**env, **(extra or {})},
                cwd=folder,
                stdout=stdout if stdout is not None else log,
                stderr=log,
                text=True,
            )

            def close():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

            cleanup.callback(close)
            return proc

        bus = start(
            "bus",
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
        )
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
        start("pw", ["pipewire"])
        wait(lambda: (runtime / "pipewire-0").exists(), "PipeWire server")
        start("wp", ["wireplumber", "-p", "policy"])
        compositor_env = {}
        if os.environ.get("LABWC_LIBRARY_PATH"):
            compositor_env["LD_LIBRARY_PATH"] = os.environ["LABWC_LIBRARY_PATH"]
        start("labwc", [labwc, "-C", str(config)], compositor_env)
        wait(lambda: (runtime / "wayland-0").exists(), "Wayland compositor")
        start(
            "portal-backend", [service_binary("xdg-desktop-portal-wlr"), "-l", "DEBUG"]
        )
        start("portal", [service_binary("xdg-desktop-portal")])
        (folder / "color").write_text("#dd527e")
        start("slide", [sys.executable, script, "--slide", str(folder)])
        wait(lambda: (folder / "slide-ready").exists(), "synthetic slide")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = start(
            "app",
            [app_path, "--wayland"],
            debug_environment(port),
        )
        page = Page(port)
        cleanup.callback(page.close)
        if workspace_tools:
            from workspace_tools import check
            check(page)
        if claude_vision:
            if meeting:
                page.click("settings")
                page.field("#provider", "sophnet")
                page.field("#asr-key", os.environ["SOPHNET_API_KEY"])
                page.click("settings-save")
                page.until("S.settings.provider==='sophnet'")
            page.click("new")
            page.field("#meeting-name", "Wayland 截图验收")
            if meeting:
                page.until("S.media.some(m=>m.path.endsWith('/QA meeting.mp4'))")
                source = page.js("S.media.find(m=>m.path.endsWith('/QA meeting.mp4')).path")
                page.field("#source-select", source)
            page.click("create")
            page.until("S.meeting.title==='Wayland 截图验收'")
            page.click("agent")
            page.field("#connect-provider", "claude")
            page.click("connect")
            page.click("session-new")
            page.until("S.agent==='ready' && S.agentName==='claude'")
            if meeting:
                session_id = page.js("S.session")
                page.click("start")
                page.until("S.capture==='idle' && !S.pending && S.events.some(e=>e.kind==='speech' && e.end>e.at)", 120)
                page.until("!S.sending && S.events.some(e=>e.kind==='assistant')", 150)
                assert page.js("!S.events.some(e=>e.kind==='user')")
        page.click("screen")
        page.click("screen-on")
        wait(lambda: (folder / "selection").is_file(), "window picker")
        print("Selected", (folder / "selection").read_text(), flush=True)

        def check_frame(index, expected):
            paths = sorted(
                data.rglob("*.jpg"), key=lambda path: path.stat().st_mtime_ns
            )
            if len(paths) < index + 1:
                return False
            with Image.open(paths[index]) as img:
                pixel = img.convert("RGB").getpixel((100, 100))
                if (
                    img.size != SIZE
                    or max(abs(a - b) for a, b in zip(pixel, expected)) > 8
                ):
                    raise AssertionError(
                        f"Frame {index+1}: {img.size}, {pixel}; expected {SIZE}, {expected}"
                    )
            print("Saved frame", index + 1, SIZE, pixel, flush=True)
            return True

        page.until(
            "document.querySelector('#screen-body .screen-image')?.naturalWidth>0"
        )
        wait(lambda: check_frame(0, (221, 82, 126)), "first slide")
        old = page.js("document.querySelector('.screen-image').src")
        (folder / "color").write_text("#288849")
        time.sleep(0.85)
        page.click("snapshot")
        page.until(
            "document.querySelector('.screen-image')?.naturalWidth>0 && document.querySelector('.screen-image').src !== "
            + json.dumps(old)
        )
        wait(lambda: check_frame(1, (40, 136, 73)), "refreshed slide")
        if claude_vision:
            page.click("frame-attach")
            page.until("S.files.length===1")
            page.field("#message", "请读取截图中央的英文数字编号，建立标题为「画面核对」的便签，在正文写入完整编号，然后回复。")
            page.click("send")
            page.until("S.notes.some(n=>n.body.includes(" + json.dumps(challenge) + "))", 150)
            page.until("!S.sending && S.events.some(e=>e.kind==='assistant')", 90)
            print("Claude read the random slide code via the portal frame and saved it in a meeting note", flush=True)
            page.click("screen")
        if worker_crash:
            # Identify the portal helper in this test application's process tree.
            candidates = []
            for procdir in Path("/proc").glob("[0-9]*"):
                try:
                    if Path(os.readlink(procdir / "exe")).name != "tingsu-portal":
                        continue
                    pid = int(procdir.name)
                    while pid > 1 and pid != process.pid:
                        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                        pid = int(stat[1])
                    if pid == process.pid:
                        candidates.append(int(procdir.name))
                except (OSError, ValueError, IndexError):
                    continue
            assert len(candidates) == 1, candidates
            import signal
            os.kill(candidates[0], signal.SIGKILL)
            page.until("!S.screen && !S.frame")
            assert process.poll() is None
            page.click("screen-on")
            page.until("document.querySelector('#screen-body .screen-image')?.naturalWidth>0")
            wait(lambda: check_frame(2, (40, 136, 73)), "recovered slide")
            print("Desktop survived capture-worker loss and reopened a fresh portal session", flush=True)
        page.click("screen-off")
        page.until(
            "!!document.querySelector('#screen-body [data-action=screen-on]') && !document.querySelector('.screen-image')"
        )
        wait(
            lambda: "session closed" in (folder / "portal-backend.log").read_text(),
            "portal release",
        )
        page.click("close", area="#dialog ")
        print("Released portal session and cleared GUI preview", flush=True)
        if meeting:
            process.terminate()
            process.wait(15)
            page.close()
            start("app-restart", [app_path, "--wayland"], debug_environment(port))
            resumed = Page(port)
            cleanup.callback(resumed.close)
            resumed.until("S.meeting.title==='Wayland 截图验收' && S.events.some(e=>e.kind==='speech') && "
                          "S.notes.some(n=>n.body.includes(" + json.dumps(challenge) + "))")
            resumed.click("agent")
            resumed.field("#connect-provider", "claude")
            resumed.click("connect")
            resumed.click("session", "继续本会议的会话")
            resumed.until("S.agent==='ready' && S.session===" + json.dumps(session_id))
            print("Verified Wayland ASR, proactive reply, screen attachment, persisted note and session after restart")


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("--slide", "--chooser"):
        return (slide if sys.argv[1] == "--slide" else chooser)(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--labwc", default="labwc")
    parser.add_argument("--claude-vision", action="store_true",
                        help="Submit a synthetic portal frame to the real, authenticated local Claude CLI.")
    parser.add_argument("--meeting", action="store_true",
                        help="Also transcribe synthetic media with Sophnet, check the proactive reply and restart the app.")
    parser.add_argument("--workspace-tools", action="store_true", help="Exercise all ten workspace changes on Wayland.")
    parser.add_argument("--worker-crash", action="store_true", help="Kill the private capture process and reselect the window.")
    args = parser.parse_args()
    if args.meeting and not (args.claude_vision and os.getenv("SOPHNET_API_KEY")):
        parser.error("--meeting requires --claude-vision and SOPHNET_API_KEY")
    with tempfile.TemporaryDirectory(prefix="tingsu-wl-") as directory:
        folder = Path(directory)
        try:
            verify(args.app.resolve(), args.labwc, folder, args.claude_vision, args.meeting, args.workspace_tools, args.worker_crash)
        except Exception:
            for path in sorted(folder.glob("*.log")):
                print(
                    path.name, path.read_text(errors="replace")[-3000:], file=sys.stderr
                )
            raise
        finally:
            # A D-Bus-activated document portal can leave a disconnected private
            # FUSE mount after its bus exits. Release it before deleting the fixture.
            documents = folder / "xdg_runtime_dir" / "doc"
            if f" {documents} " in Path("/proc/self/mountinfo").read_text():
                subprocess.run(["fusermount3", "-uz", str(documents)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
