"""KDE Plasma Wayland window capture regression (MYS-3027 / MYS-3030).

Brings up a private KWin Wayland session with KDE's own ScreenCast backend and
chrome-test profile, launches the AppImage under test with **no Tingsu launch
arguments**, and drives the real workbench the way a user does:

    窗口画面 → KDE system picker → select the Chrome window → Share
    → first frame → change the page content from A to B in the same window
    → 刷新当前画面 → stop → select the same window again

Every step is asserted against the bytes the product actually wrote:

* the first frame must be a **new** JPEG, 640x488 (the 640x460 client area plus
  this KWin's decorations) whose pixels are pattern A;
* the refresh must produce a **different** JPEG whose pixels are pattern B -
  a script that passes "a frame exists" cannot pass this;
* the workbench preview must show the same pattern as the newest JPEG;
* after 停止共享 the portal session must be released, and re-selecting the same
  window must yield another fresh frame.

The process exits non-zero unless every assertion holds, so the official v2.4.0
package (which fails buffer negotiation with -22 and writes no JPEG) is reported
as a failure by construction rather than by inspection.

Run it inside a private PID namespace and private session bus/display, e.g.

    tests/e2e/kde_window_capture_isolated.sh --app dist/Tingsu-Linux-x86_64.AppImage \\
        --label candidate --expect pass --out /path/to/report

Standalone use (an already isolated session):

    python3 tests/e2e/kde_window_capture.py --app <AppImage> --label <name> \\
        --out <dir> --expect pass --fakeinput <kdefakeinput> --shot <kdeshot.py>
"""

import argparse
import contextlib
import http.server
import json
import os
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TITLE = "Synthetic Chrome capture"
CLIENT = (640, 460)          # Chrome's client area, as requested below.
DECORATION_HEIGHT = 28       # KWin's title bar; the picker shares the outer window.
OUTER = (CLIENT[0], CLIENT[1] + DECORATION_HEIGHT)
SLIDE_A = "#dd527e"
SLIDE_B = "#288849"
RGB_A = (221, 82, 126)
RGB_B = (40, 136, 73)
SCREEN = (1920, 1080)
PIXEL_TOLERANCE = 8

# The private output is 1920x1080 and KWin places the workbench at a fixed spot;
# these are the anchors the same session produced in MYS-3028. Every click is
# followed by a state assertion, so a shifted layout fails the step instead of
# silently clicking empty space.
CAPTURE_BUTTON = (1290, 223)
# The workbench's 会议画面 dialog is modal and covers the toolbar, so its own
# buttons drive refresh and stop. They are located from each screenshot by their
# colours instead of a guessed offset: the primary action is the accent-filled
# row, secondary actions are the plain rows below it.
BUTTON_ACCENT = (53, 132, 228)
BUTTON_PLAIN = (246, 245, 244)
DIALOG_SPAN = (600, 1320)     # x window the dialog occupies on this output
PREVIEW_SAMPLE = (700, 500)   # inside the preview image, clear of the caption text
# The KDE ScreenCast picker tiles, in the same output coordinates.
PICKER_CHROME = (775, 676)
PICKER_SHARE = (1252, 908)

PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<body style="margin:0;background:{color};width:{w}px;height:{h}px;overflow:hidden">
<div id="label" style="font:bold 46px sans-serif;color:#fff;text-align:center;padding-top:{pad}px">{label}</div>
<script>
// The slide follows the file the test writes, so switching A -> B needs no
// browser interaction and the next captured frame is the only way to see B.
let shown = {color!r};
async function poll() {{
  try {{
    const r = await fetch('/state', {{cache: 'no-store'}});
    const v = await r.text();
    if (v !== shown) {{
      shown = v;
      document.body.style.background = v;
      document.getElementById('label').textContent =
        v === {a!r} ? 'PATTERN A' : 'PATTERN B';
    }}
  }} catch (e) {{}}
  setTimeout(poll, 100);
}}
poll();
</script>
</body>"""


def service_binary(name):
    for directory in ("/usr/lib", "/usr/libexec", "/usr/lib/xdg-desktop-portal"):
        path = Path(directory) / name
        if path.is_file():
            return path
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(name)


def versions(app, chrome, env):
    def run(argv):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=40,
                                 env=env)
            return (out.stdout + out.stderr).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return f"error: {exc}"

    return {
        "app_version": run([str(app), "--version"]),
        "chrome": run([chrome, "--version"]),
        "kwin": run(["kwin_wayland", "--version"]),
        "xdg-desktop-portal": run([str(service_binary("xdg-desktop-portal")), "--version"]),
        "xdg-desktop-portal-kde": run(["pacman", "-Q", "xdg-desktop-portal-kde"]),
        "plasma-desktop": run(["pacman", "-Q", "plasma-desktop"]),
        "pipewire": run(["pipewire", "--version"]),
        "gstreamer": run(["gst-launch-1.0", "--version"]),
        "host_os": run(["sh", "-c", ". /etc/os-release; echo $PRETTY_NAME"]),
    }


def serve_slide(folder, holder):
    """A loopback A/B page; the test flips the colour through a file."""
    page_a = PAGE.format(title=TITLE, color=SLIDE_A, w=CLIENT[0], h=CLIENT[1],
                         pad=180, label="PATTERN A", a=SLIDE_A, b=SLIDE_B)
    body = page_a.encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/state"):
                data = holder["state"].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    server.allow_reuse_address = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--expect", choices=("pass", "fail"), default="pass",
                        help="fail: the package must reproduce the defect (no JPEG)")
    parser.add_argument("--chrome", default="google-chrome-stable")
    parser.add_argument("--fakeinput", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--software-gl", action="store_true",
                        help="force LIBGL_ALWAYS_SOFTWARE=1 in the private session")
    args = parser.parse_args()

    folder = (args.out / args.label).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    app = args.app.resolve()
    report = {
        "label": args.label,
        "expectation": args.expect,
        "app": str(app),
        "app_sha256": subprocess.run(["sha256sum", str(app)], capture_output=True,
                                     text=True).stdout.split()[0],
        "app_size": app.stat().st_size,
        "launch": f"{app.name}   (no Tingsu launch arguments)",
        "client_area": list(CLIENT),
        "expected_outer_size": list(OUTER),
        "steps": [],
        "checks": [],
    }

    env = os.environ.copy()
    for name in ("DISPLAY", "WAYLAND_SOCKET", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                 "PIPEWIRE_REMOTE", "PIPEWIRE_RUNTIME_DIR", "PULSE_SERVER",
                 "PULSE_RUNTIME_PATH", "KDE_FULL_SESSION", "SESSION_MANAGER",
                 "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORMTHEME",
                 "XDG_SESSION_DESKTOP", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
                 "KWIN_DRM_DEVICES", "WAYLAND_DISPLAY", "APPIMAGE", "APPDIR"):
        env.pop(name, None)
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        path = folder / name.lower()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[name] = str(path)
    runtime = Path(tempfile.mkdtemp(prefix="tgk-", dir=env.get("TMPDIR") or "/tmp"))
    runtime.chmod(0o700)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env.update(
        XDG_CURRENT_DESKTOP="KDE", XDG_SESSION_DESKTOP="KDE", XDG_SESSION_TYPE="wayland",
        WAYLAND_DISPLAY="wayland-0", QT_QPA_PLATFORM="wayland",
        LANG="C.UTF-8", LC_ALL="C.UTF-8", KDE_FULL_SESSION="true",
    )
    if args.software_gl:
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    report["namespace"] = {
        "pid": os.readlink("/proc/self/ns/pid"),
        "net": os.readlink("/proc/self/ns/net"),
        "mount": os.readlink("/proc/self/ns/mnt"),
        "proc_entries": len([p for p in os.listdir("/proc") if p.isdigit()]),
        "host_pid_namespace": os.environ.get("TINGSU_HOST_PID_NS"),
    }
    report["versions"] = versions(app, args.chrome, env)

    config = Path(env["XDG_CONFIG_HOME"])
    data = Path(env["XDG_DATA_HOME"]) / "tingsu"
    frames_dir = data / "frames"
    for name in ("xdg-desktop-portal", "xdg-desktop-portal-kde"):
        (config / name).mkdir(parents=True, exist_ok=True)
    (config / "xdg-desktop-portal/portals.conf").write_text(
        "[preferred]\ndefault=none\norg.freedesktop.impl.portal.ScreenCast=kde\n")

    def check(name, ok, detail=None):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""),
              flush=True)
        return bool(ok)

    def step(name, ok, detail=None):
        report["steps"].append({"step": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""),
              flush=True)
        return bool(ok)

    holder = {"state": SLIDE_A}
    server, page_url = serve_slide(folder, holder)
    report["page_url"] = page_url

    with contextlib.ExitStack() as cleanup:

        def start(name, argv, extra=None, stdout=None):
            log = cleanup.enter_context((folder / (name + ".log")).open("w"))
            proc = subprocess.Popen(argv, env={**env, **(extra or {})}, cwd=folder,
                                    stdout=stdout if stdout is not None else log,
                                    stderr=log, text=True)
            cleanup.callback(lambda: (proc.terminate(),
                                      proc.wait(timeout=15) if proc.poll() is None else None))
            return proc

        def wait(what, fn, seconds=45):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                try:
                    value = fn()
                except (OSError, sqlite3.Error, ValueError, subprocess.SubprocessError):
                    value = None
                if value:
                    return value
                time.sleep(0.2)
            raise TimeoutError(f"{args.label}: {what}")

        def shot(name):
            path = folder / (name + ".png")
            result = subprocess.run([sys.executable, args.shot, str(path)],
                                    env=env, capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not path.exists():
                return None
            return path

        def sample(path, point):
            from PIL import Image
            with Image.open(path) as image:
                image.load()
                return image.convert("RGB").getpixel(point)

        def buttons(path):
            """Locate the 会议画面 dialog's action rows by their fill colour.

            Returns {"accent": [(y0, y1)], "plain": [(y0, y1)]} in output rows,
            scanning only the dialog's x span so workbench chrome cannot match.
            """
            from PIL import Image
            import numpy as np
            with Image.open(path) as image:
                pixels = np.asarray(image.convert("RGB")).astype(int)
            x0, x1 = DIALOG_SPAN
            span = pixels[:, x0:x1]

            def rows(target, tol, minimum):
                close = (abs(span[:, :, 0] - target[0]) < tol) & \
                        (abs(span[:, :, 1] - target[1]) < tol) & \
                        (abs(span[:, :, 2] - target[2]) < tol)
                counts = close.sum(axis=1)
                found, start = [], None
                for y, count in enumerate(counts):
                    if count >= minimum and start is None:
                        start = y
                    elif count < minimum and start is not None:
                        if y - start >= 8:
                            found.append((start, y - 1))
                        start = None
                if start is not None and len(counts) - start >= 8:
                    found.append((start, len(counts) - 1))
                return found

            return {"accent": rows(BUTTON_ACCENT, 12, 200),
                    "plain": rows(BUTTON_PLAIN, 4, 200)}

        def dialog(path):
            """Read which actions the 会议画面 dialog currently offers.

            The dialog has one accent-filled primary row and, only while a window
            is actually shared, plain rows below it (停止共享, and 将当前画面附到消息
            once a frame exists). Reading that shape keeps the test from clicking a
            button that is not there - which is exactly what the broken package
            leaves behind when the stream dies.
            """
            found = buttons(path)
            return {"accent": found["accent"], "plain": found["plain"],
                    "shared": bool(found["accent"]) and len(found["plain"]) > 0}

        def press(path, which, index=0):
            """Click a located row of the dialog; None when the row is not offered."""
            found = buttons(path)[which] if path is not None else []
            if not found:
                return None
            index = index if index >= 0 else len(found) + index
            if not 0 <= index < len(found):
                return None
            y0, y1 = found[index]
            point = ((DIALOG_SPAN[0] + DIALOG_SPAN[1]) // 2, (y0 + y1) // 2)
            send(f"click {point[0]} {point[1]}", 0.5)
            return point

        def close(a, b):
            return max(abs(x - y) for x, y in zip(a, b)) <= PIXEL_TOLERANCE

        fake = [None]

        def send(command, settle=0.7):
            if fake[0] is None:
                log = cleanup.enter_context((folder / "fakeinput.log").open("w"))
                fake[0] = subprocess.Popen([args.fakeinput], env=env, cwd=folder,
                                           stdin=subprocess.PIPE, stdout=log, stderr=log,
                                           text=True)
                cleanup.callback(fake[0].terminate)
                wait("fake-input ready",
                     lambda: (folder / "fakeinput.log").read_text().strip(), 25)
            fake[0].stdin.write(command + "\n")
            fake[0].stdin.flush()
            time.sleep(settle)

        bus = start("bus", ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                    stdout=subprocess.PIPE)
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
        start("pipewire", ["pipewire"])
        wait("PipeWire socket", lambda: (runtime / "pipewire-0").exists())
        start("wireplumber", ["wireplumber", "-p", "policy"])
        start("kwin", ["kwin_wayland", "--virtual", "--socket", "wayland-0",
                       "--width", str(SCREEN[0]), "--height", str(SCREEN[1])])
        wait("KWin Wayland socket", lambda: (runtime / "wayland-0").exists(), 120)
        time.sleep(3)
        step("isolated KWin Wayland session started", True, report["versions"]["kwin"])

        start("portal-kde", [str(service_binary("xdg-desktop-portal-kde"))])
        start("portal", [str(service_binary("xdg-desktop-portal"))])
        time.sleep(2)
        probe = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop", "--method",
             "org.freedesktop.DBus.Properties.Get", "org.freedesktop.portal.ScreenCast",
             "AvailableSourceTypes"], env=env, capture_output=True, text=True, timeout=40)
        mask = re.search(r"uint32\s+(\d+)", probe.stdout)
        bitmask = int(mask.group(1)) if mask else None
        step("KDE ScreenCast offers window sources", bool(bitmask and bitmask & 2),
             f"raw={(probe.stdout + probe.stderr).strip()}")

        profile = config / "chrome-test-profile"
        profile.mkdir(mode=0o700, exist_ok=True)
        chrome_log = cleanup.enter_context((folder / "chrome.log").open("w"))
        chrome = subprocess.Popen(
            [args.chrome, f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--ozone-platform=wayland",
             f"--app={page_url}", f"--window-size={CLIENT[0]},{CLIENT[1]}",
             "--window-position=1300,120", "--disable-gpu-sandbox",
             "--disable-features=DialMediaRouteProvider"],
            env=env, cwd=folder, stdout=chrome_log, stderr=chrome_log, text=True)
        cleanup.callback(lambda: (chrome.terminate(), chrome.wait(timeout=20)))
        time.sleep(12)
        step("Chrome window open on the A pattern with its native Wayland backend",
             chrome.poll() is None,
             f"{report['versions']['chrome']} --ozone-platform=wayland")

        app_proc = start("app", [str(app)])
        wait("meeting database",
             lambda: sqlite3.connect(data / "studio.sqlite3").execute(
                 "select count(*) from meetings").fetchone()[0] >= 0)
        time.sleep(3)
        step("Tingsu started with no launch arguments", app_proc.poll() is None,
             f"rc={app_proc.poll()}")
        first = shot("01-desktop")
        step("captured the private desktop", first is not None, first.name if first else None)

        def frames():
            if not frames_dir.is_dir():
                return []
            return sorted(frames_dir.glob("*/screen-*.jpg"),
                          key=lambda p: p.stat().st_mtime_ns)

        before = {p.name for p in frames()}

        def new_frames():
            return [p for p in frames() if p.name not in before]

        # --- the user's path: 窗口画面 -> KDE picker -> the Chrome tile -> Share ---
        send(f"click {CAPTURE_BUTTON[0]} {CAPTURE_BUTTON[1]}", 4.0)
        picker = shot("02-kde-picker")
        step("KDE system ScreenCast picker opened from the workbench", picker is not None,
             picker.name if picker else None)
        if picker is not None:
            tile = sample(picker, PICKER_CHROME)
            check("picker lists the test Chrome window (PATTERN A thumbnail)",
                  close(tile, RGB_A), f"tile pixel={list(tile)} expected={list(RGB_A)}")
        send(f"click {PICKER_CHROME[0]} {PICKER_CHROME[1]}", 1.5)
        shot("03-picker-chrome-selected")
        send(f"click {PICKER_SHARE[0]} {PICKER_SHARE[1]}", 10.0)
        after_share = shot("04-after-share")
        report["workbench_after_share"] = after_share.name if after_share else None

        try:
            frame_a = wait("a first frame", lambda: new_frames() and new_frames()[0], 60)
        except TimeoutError:
            frame_a = None
        report["first_frame"] = None
        if frame_a is not None:
            from PIL import Image
            with Image.open(frame_a) as image:
                image.load()
                pixel = image.convert("RGB").getpixel((100, 100))
                report["first_frame"] = {
                    "file": frame_a.name, "size": list(image.size),
                    "pixel": list(pixel), "jpeg_bytes": frame_a.stat().st_size,
                    "sha256": subprocess.run(["sha256sum", str(frame_a)],
                                             capture_output=True, text=True).stdout.split()[0],
                }
            okSize = tuple(report["first_frame"]["size"]) == OUTER
            okPixel = close(pixel, RGB_A)
            step("first frame is the selected Chrome window at 640x488 with pattern A",
                 okSize and okPixel,
                 json.dumps(report["first_frame"], ensure_ascii=False))
            if after_share is not None:
                preview = sample(after_share, PREVIEW_SAMPLE)
                report["preview_first"] = list(preview)
                check("workbench preview shows pattern A", close(preview, RGB_A),
                      f"preview pixel={list(preview)}")
        else:
            step("first frame is the selected Chrome window at 640x488 with pattern A",
                 False, f"no new JPEG under {frames_dir}")

        # --- B in the same window, then the dialog's own 刷新当前画面 ---
        holder["state"] = SLIDE_B
        time.sleep(1.5)
        if after_share is not None:
            shape = dialog(after_share)
            step("after the grant the workbench still reports a shared window",
                 shape["shared"],
                 f"dialog rows: accent={shape['accent']} plain={shape['plain']}")
        refreshed_point = press(after_share, "accent") if after_share else None
        step("the dialog offered 刷新当前画面 to click", refreshed_point is not None,
             f"point={refreshed_point}")
        time.sleep(8)
        refreshed = [p for p in new_frames() if frame_a is None or p.name != frame_a.name]
        report["refreshed_frame"] = None
        after_refresh = shot("06-after-refresh")
        if refreshed:
            from PIL import Image
            target = refreshed[-1]
            with Image.open(target) as image:
                image.load()
                pixel = image.convert("RGB").getpixel((100, 100))
                report["refreshed_frame"] = {
                    "file": target.name, "size": list(image.size),
                    "pixel": list(pixel), "jpeg_bytes": target.stat().st_size,
                    "sha256": subprocess.run(["sha256sum", str(target)],
                                             capture_output=True, text=True).stdout.split()[0],
                }
            okSize = tuple(report["refreshed_frame"]["size"]) == OUTER
            step("刷新当前画面 produced a new JPEG that decodes to pattern B",
                 okSize and close(pixel, RGB_B),
                 json.dumps(report["refreshed_frame"], ensure_ascii=False))
            if after_refresh is not None:
                preview = sample(after_refresh, PREVIEW_SAMPLE)
                report["preview_refreshed"] = list(preview)
                check("workbench preview switched to pattern B", close(preview, RGB_B),
                      f"preview pixel={list(preview)}")
        else:
            step("刷新当前画面 produced a new JPEG that decodes to pattern B", False,
                 f"frames before={sorted(p.name for p in frames())}")

        # --- stop, confirm the portal released the session, then select again ---
        # Release is proved from the portal itself: the session object it handed
        # out stops existing once the grant is dropped. Watching the whole private
        # bus (this bus carries only our test traffic) shows both the session
        # handle from the grant and the Closed signal that ends it.
        monitor_log = cleanup.enter_context((folder / "dbus-monitor.log").open("w"))
        monitor = subprocess.Popen(["dbus-monitor", "--session"], env=env, cwd=folder,
                                   stdout=monitor_log, stderr=subprocess.STDOUT, text=True)
        cleanup.callback(monitor.terminate)
        time.sleep(1)

        before_stop = after_refresh or after_share
        stopped_point = press(before_stop, "plain", index=-1) \
            if before_stop else None
        step("the dialog offered 停止共享 to click", stopped_point is not None,
             f"point={stopped_point}")
        released = False
        if stopped_point is not None:
            for _ in range(60):
                time.sleep(1.0)
                monitor_log.flush()
                text = (folder / "dbus-monitor.log").read_text(errors="replace")
                if "member=Closed" in text and "portal.Session" in text:
                    released = True
                    break
        stopped = shot("07-after-stop")
        report["after_stop"] = stopped.name if stopped else None
        report["session_closed_signal"] = released
        step("stopping closes the portal ScreenCast session", released,
             "org.freedesktop.portal.Session.Closed observed on the private bus"
             if released else "no Portal Session Closed signal observed")
        if stopped is not None:
            # With the session gone the dialog must offer 选择共享窗口 again and the
            # toolbar must drop back to its unshared caption.
            report["toolbar_after_stop"] = list(sample(stopped, CAPTURE_BUTTON))
            shape = dialog(stopped)
            check("after stopping the dialog offers 选择共享窗口, not 停止共享",
                  bool(shape["accent"]) and not shape["shared"],
                  f"accent rows={shape['accent']} plain rows={shape['plain']}")

        # Re-select the same window from the dialog that is still open.
        before = {p.name for p in frames()}
        reselect_point = press(stopped, "accent") if stopped else None
        step("the dialog offered 选择共享窗口 to re-select", reselect_point is not None,
             f"point={reselect_point}")
        # Only a picker that actually opened can report what it offers; without
        # that precondition the shot below would still show the stale frame from
        # the previous leg and read as a pass.
        picker_again = None
        if reselect_point is not None:
            time.sleep(5)
            picker_again = shot("08-picker-reselect")
            if picker_again is not None:
                tile = sample(picker_again, PICKER_CHROME)
                check("the picker offers the same Chrome window again", close(tile, RGB_B),
                      f"tile pixel={list(tile)} expected={list(RGB_B)}")
            send(f"click {PICKER_CHROME[0]} {PICKER_CHROME[1]}", 1.5)
            send(f"click {PICKER_SHARE[0]} {PICKER_SHARE[1]}", 10.0)
            shot("09-after-reselect")
        else:
            step("the picker offers the same Chrome window again", False,
                 "选择共享窗口 was never offered, so no picker could open")
        try:
            again = wait("a frame after re-selecting the window",
                         lambda: new_frames() and new_frames()[-1], 60) \
                if picker_again is not None else None
        except TimeoutError:
            again = None
        report["reselect_frame"] = None
        if again is not None:
            from PIL import Image
            with Image.open(again) as image:
                image.load()
                pixel = image.convert("RGB").getpixel((100, 100))
                report["reselect_frame"] = {"file": again.name, "size": list(image.size),
                                            "pixel": list(pixel)}
            step("re-selecting the same Chrome window captures pattern B again",
                 tuple(report["reselect_frame"]["size"]) == OUTER and close(pixel, RGB_B),
                 json.dumps(report["reselect_frame"], ensure_ascii=False))
        else:
            step("re-selecting the same Chrome window captures pattern B again", False,
                 "no new JPEG after re-selecting")

        report["app_log"] = (folder / "app.log").read_text(errors="replace")[-4000:]
        report["pipewire_log_tail"] = (folder / "pipewire.log").read_text(
            errors="replace")[-4000:]
        report["portal_kde_log_tail"] = (folder / "portal-kde.log").read_text(
            errors="replace")[-2000:]

    server.shutdown()
    shutil.rmtree(runtime, ignore_errors=True)

    report["frames"] = [{"file": p.name, "bytes": p.stat().st_size}
                        for p in frames()]
    negotiation = [line for line in report["pipewire_log_tail"].splitlines()
                   if "Buffers" in line or "Invalid argument" in line]
    report["negotiation_log"] = negotiation

    if args.expect == "fail":
        ok = check("the package wrote no frame for the selected window",
                   not report["first_frame"], f"first_frame={report['first_frame']}")
        ok = check("the raw negotiation log shows the rejected buffer params",
                   any("Invalid argument" in line for line in negotiation)) and ok
        report["ok"] = ok
        print(f"[EXPECT-fail] {'reproduced as expected' if ok else 'did NOT reproduce'}",
              flush=True)
        (args.out / f"{args.label}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        return 0 if ok else 1

    report["ok"] = all(entry["ok"] for entry in report["steps"]) and \
        all(entry["ok"] for entry in report["checks"])
    (args.out / f"{args.label}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
