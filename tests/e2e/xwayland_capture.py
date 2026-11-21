"""Exercise the XWayland window picker in a private headless Weston desktop.

Pass --wemeet /usr/bin/wemeet to also select the real Tencent Meeting login window.
The test uses an empty HOME and does not sign in or enter a meeting.
"""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from PIL import Image
from desktop import Page, wait
from wayland_window import SIZE, TITLE


def verify(app, folder, wemeet):
    env = dict(os.environ)
    for key in ("DISPLAY", "WAYLAND_SOCKET", "XAUTHORITY", "SESSION_MANAGER", "DBUS_SESSION_BUS_ADDRESS",
                "PULSE_SERVER", "PIPEWIRE_REMOTE", "CODEX_HOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORMTHEME"):
        env.pop(key, None)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        path = folder / key.lower()
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env.update(WAYLAND_DISPLAY="wayland-test", XDG_SESSION_TYPE="wayland", QT_QPA_PLATFORM="wayland",
               LIBGL_ALWAYS_SOFTWARE="1", LC_ALL="C.UTF-8", XDG_CURRENT_DESKTOP="weston")
    with ExitStack() as cleanup:
        def start(name, args, extra=None, stdout=None):
            log = cleanup.enter_context((folder / (name + ".log")).open("w"))
            proc = subprocess.Popen(args, cwd=folder, env={**env, **(extra or {})}, text=True,
                                    stdout=stdout or log, stderr=log, start_new_session=True)

            def close():
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()

            cleanup.callback(close)
            return proc

        bus = start("bus", ["dbus-daemon", "--session", "--nofork", "--print-address=1"], stdout=subprocess.PIPE)
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
        start("weston", ["weston", "--backend=headless", "--shell=desktop", "--width=1440", "--height=960",
                         "--socket=" + env["WAYLAND_DISPLAY"], "--xwayland", "--idle-time=0"])
        display = wait(lambda: re.search(r"xserver listening on display (:\d+)", (folder / "weston.log").read_text()),
                       "nested XWayland display")
        env["DISPLAY"] = display[1]
        (folder / "color").write_text("#dd527e")
        slide = start("slide", [sys.executable, str(Path(__file__).with_name("wayland_window.py").resolve()),
                                "--slide", str(folder)], {"QT_QPA_PLATFORM": "xcb"})
        wait(lambda: (folder / "slide-ready").exists(), "XWayland test window")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = start("app", [app, "--wayland"], {"QTWEBENGINE_REMOTE_DEBUGGING": f"127.0.0.1:{port}"})
        page = Page(port)
        cleanup.callback(page.close)
        data = Path(env["XDG_DATA_HOME"]) / "tingsu" / "frames"

        def frame(index):
            paths = sorted(data.rglob("*.jpg"), key=lambda path: path.stat().st_mtime_ns)
            if len(paths) <= index:
                return None
            with Image.open(paths[index]) as img:
                return img.convert("RGB")

        def select(title):
            page.click("screen-xwindows")
            page.click("screen-xwindow", match=title)
            page.until("document.querySelector('#screen-body .screen-image')?.naturalWidth>0")

        def assert_slide(index, expected):
            img = wait(lambda: frame(index), "saved XWayland frame")
            pixel = img.getpixel((100, 100))
            assert img.size == SIZE and max(abs(a-b) for a, b in zip(pixel, expected)) <= 8, (img.size, pixel)
            print("Selected-window frame", index+1, img.size, pixel, flush=True)

        page.click("screen")
        select(TITLE)
        assert_slide(0, (221, 82, 126))
        old = page.js("S.frame")
        (folder / "color").write_text("#288849")
        time.sleep(0.4)
        page.click("snapshot")
        page.until("S.frame && S.frame!==" + json.dumps(old))
        assert_slide(1, (40, 136, 73))
        slide.terminate()
        slide.wait(5)
        page.click("snapshot")
        page.until("!S.screen && !S.frame")
        assert process.poll() is None
        print("Closed target cleared the share and left the desktop running", flush=True)

        if wemeet:
            start("wemeet", [wemeet])
            def login_window():
                tree = subprocess.check_output(["xwininfo", "-root", "-tree"], env=env, text=True)
                for line in tree.splitlines():
                    match = re.search(r'(0x[0-9a-fA-F]+) "腾讯会议": .*?(\d+)x(\d+)\+', line)
                    # Distinguish the transient square splash from the mapped login window.
                    if match and int(match[3]) > int(match[2]):
                        return int(match[2]), int(match[3])
            expected_size = wait(login_window, "Tencent Meeting login window", 30)
            select("腾讯会议")
            img = wait(lambda: frame(2), "Tencent Meeting capture")
            assert abs(img.width - expected_size[0]) <= 1 and abs(img.height - expected_size[1]) <= 1
            assert max(hi-lo for lo, hi in img.getextrema()) > 64
            print("Captured the real Tencent Meeting login window", expected_size, "as", img.size, flush=True)
            page.click("screen-off")
            page.until("!S.screen && !S.frame")
    if wemeet:
        # The distro's bubblewrap launcher starts its own session. Wait for its helpers to
        # finish writing their private test profile before the temporary directory is removed.
        marker = os.fsencode("HOME=" + env["HOME"])
        def helpers_gone():
            for entry in Path("/proc").glob("[0-9]*/environ"):
                try:
                    if marker in entry.read_bytes().split(b"\0"):
                        return False
                except OSError:
                    continue
            return True
        wait(helpers_gone, "private Tencent Meeting helpers to exit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--wemeet", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tingsu-xwayland-") as directory:
        verify(args.app.resolve(), Path(directory), args.wemeet)
