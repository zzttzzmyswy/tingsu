"""wlroots (labwc) window capture regression for the shipped AppImage.

The KDE path is covered by `kde_window_capture.py`; this is the already-supported
wlroots path, checked on the candidate package so the buffer-floor change cannot
regress it. Same assertions, same strength:

* first frame is a new JPEG at the target window's size showing pattern A;
* changing the window to B and pressing 刷新当前画面 yields a **different** JPEG
  whose pixels are B, and the preview follows;
* 停止共享 releases the portal session, and re-selecting the window captures B.

Unlike the KDE path this one publishes the window's own default size, so the
frame size assertion uses the client size rather than the decorated outer size.

Run inside a private namespace:

    tests/e2e/wlroots_window_capture_isolated.sh \
        --app dist/Tingsu-Linux-x86_64.AppImage --label wlroots \
        --labwc /path/labwc --labwc-lib /path/lib --wtype /path/wtype \
        --pointer /path/wlrpointer --out /path/to/report

Standalone:

    python3 tests/e2e/wlroots_window_capture.py --app <AppImage> --label <name> \\
        --out <dir> --labwc <labwc> [--labwc-lib <dir>] --wtype <wtype> \\
        --pointer <wlrpointer>
"""

import argparse
import contextlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

TITLE = "Synthetic Wayland meeting"
SIZE = (640, 460)
SLIDE_A = "#dd527e"
SLIDE_B = "#288849"
RGB_A = (221, 82, 126)
RGB_B = (40, 136, 73)
SCREEN = (1280, 720)
PIXEL_TOLERANCE = 8
# The toolbar's 窗口画面 drop-down in the private output.
CAPTURE_MENU = (950, 92)


def slide(folder):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication([])
    window = QWidget(None, Qt.WindowType.FramelessWindowHint)
    window.setWindowTitle(TITLE)
    window.setFixedSize(*SIZE)
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
    """A dmenu-compatible picker driven by real widget clicks on the private seat."""
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
    with (folder / "chooser-rows").open("a") as log:
        log.write(json.dumps(rows) + "\n")
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
        raise RuntimeError(f"the portal did not offer the synthetic window: {rows}")
    entry = sources.item(target)

    def select():
        sources.scrollToItem(entry)
        QTest.mouseClick(sources.viewport(), Qt.MouseButton.LeftButton,
                         pos=sources.visualItemRect(entry).center())
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok),
                         Qt.MouseButton.LeftButton)

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


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("--slide", "--chooser"):
        return (slide if sys.argv[1] == "--slide" else chooser)(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--label", default="wlroots")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--labwc", required=True)
    parser.add_argument("--labwc-lib", default="")
    parser.add_argument("--wtype", required=True)
    parser.add_argument("--pointer", required=True)
    args = parser.parse_args()

    from PIL import Image

    folder = (args.out / args.label).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    app = args.app.resolve()
    report = {
        "label": args.label,
        "app": str(app),
        "app_sha256": subprocess.run(["sha256sum", str(app)], capture_output=True,
                                     text=True).stdout.split()[0],
        "app_size": app.stat().st_size,
        "launch": f"{app.name}   (no Tingsu launch arguments)",
        "expected_size": list(SIZE),
        "namespace": {
            "pid": os.readlink("/proc/self/ns/pid"),
            "net": os.readlink("/proc/self/ns/net"),
            "mount": os.readlink("/proc/self/ns/mnt"),
            "proc_entries": len([p for p in os.listdir("/proc") if p.isdigit()]),
            "host_pid_namespace": os.environ.get("TINGSU_HOST_PID_NS"),
        },
        "steps": [],
    }

    env = os.environ.copy()
    for name in ("DISPLAY", "WAYLAND_SOCKET", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                 "PIPEWIRE_REMOTE", "PIPEWIRE_RUNTIME_DIR", "PULSE_SERVER",
                 "PULSE_RUNTIME_PATH", "KDE_FULL_SESSION", "SESSION_MANAGER",
                 "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORMTHEME",
                 "XDG_SESSION_DESKTOP", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
                 "APPIMAGE", "APPDIR"):
        env.pop(name, None)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME",
                 "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        path = folder / name.lower()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[name] = str(path)
    # PipeWire and Wayland socket paths are capped at 108 bytes, and a workspace
    # path is longer, so the runtime dir goes under the short /tmp mount.
    runtime = Path(tempfile.mkdtemp(prefix="tgw-", dir=env.get("TMPDIR") or "/tmp"))
    runtime.chmod(0o700)
    env["XDG_RUNTIME_DIR"] = str(runtime)
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
    runtime = Path(env["XDG_RUNTIME_DIR"])
    config = Path(env["XDG_CONFIG_HOME"])
    data = Path(env["XDG_DATA_HOME"]) / "tingsu"
    for name in ("xdg-desktop-portal", "xdg-desktop-portal-wlr"):
        (config / name).mkdir(exist_ok=True)
    (config / "xdg-desktop-portal/portals.conf").write_text(
        "[preferred]\ndefault=none\norg.freedesktop.impl.portal.ScreenCast=wlr\n")
    # xdg-desktop-portal-wlr caps chooser_cmd at ~127 bytes and a workspace path
    # is longer, so the picker goes through a short wrapper in the runtime dir.
    script = str(Path(__file__).resolve())
    chooser_sh = runtime / "chooser.sh"
    chooser_sh.write_text("#!/bin/sh\nexec %s\n" % shlex.join(
        [sys.executable, script, "--chooser", str(folder)]))
    chooser_sh.chmod(0o755)
    (config / "xdg-desktop-portal-wlr/config").write_text(
        f"[screencast]\nchooser_type=dmenu\nchooser_cmd={chooser_sh}\nmax_fps=10\n")
    # F12 raises the workbench: it only maps its surface after its database opens.
    rc = ET.Element("labwc_config")
    bind = ET.SubElement(ET.SubElement(rc, "keyboard"), "keybind", key="F12")
    each = ET.SubElement(bind, "action", name="ForEach")
    ET.SubElement(ET.SubElement(each, "query"), "title").text = "*会议工作台*"
    actions = ET.SubElement(each, "then")
    ET.SubElement(actions, "action", name="Focus")
    ET.SubElement(actions, "action", name="Raise")
    ET.SubElement(actions, "action", name="Execute",
                  command=shlex.join(["touch", str(folder / "workbench-mapped")]))
    ET.ElementTree(rc).write(config / "rc.xml", encoding="utf-8")

    def check(name, ok, detail=None):
        report["steps"].append({"step": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""),
              flush=True)
        return bool(ok)

    with contextlib.ExitStack() as cleanup:

        def start(name, argv, extra=None, stdout=None):
            log = cleanup.enter_context((folder / (name + ".log")).open("w"))
            proc = subprocess.Popen(argv, env={**env, **(extra or {})}, cwd=folder,
                                    stdout=stdout if stdout is not None else log,
                                    stderr=log, text=True)

            def close():
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

            cleanup.callback(close)
            return proc

        def wait(what, fn, seconds=60):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    value = fn()
                except (OSError, sqlite3.Error, ValueError, subprocess.SubprocessError):
                    value = None
                if value:
                    return value
                time.sleep(0.15)
            raise TimeoutError(f"{args.label}: {what}")

        def key(*argv):
            subprocess.run([args.wtype, *argv], env=env, timeout=20, check=True)
            time.sleep(0.4)

        def shortcut(combo):
            key("-M", "ctrl", "-k", combo[-1], "-m", "ctrl")

        def type_text(text):
            subprocess.run([args.wtype, "--", text], env=env, timeout=20, check=True)
            time.sleep(0.2)

        def shot(name):
            path = folder / (name + ".png")
            subprocess.run(["grim", str(path)], env=env, timeout=30, check=True)
            return path

        pointer = [None]

        def open_pointer():
            if pointer[0] is not None:
                return
            log = cleanup.enter_context((folder / "pointer.log").open("w"))
            pointer[0] = subprocess.Popen([args.pointer], env=env, cwd=folder, text=True,
                                          stdin=subprocess.PIPE, stdout=log, stderr=log)
            cleanup.callback(pointer[0].terminate)
            wait("virtual pointer ready",
                 lambda: (folder / "pointer.log").read_text().strip(), 20)
            time.sleep(0.8)

        def click(x, y):
            open_pointer()
            pointer[0].stdin.write(f"extent {SCREEN[0]} {SCREEN[1]}\nclick {x} {y}\n")
            pointer[0].stdin.flush()
            time.sleep(0.9)

        bus = start("bus", ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                    stdout=subprocess.PIPE)
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
        start("pipewire", ["pipewire"])
        wait("PipeWire socket", lambda: (runtime / "pipewire-0").exists())
        start("wireplumber", ["wireplumber", "-p", "policy"])
        compositor_env = {"LD_LIBRARY_PATH": args.labwc_lib} if args.labwc_lib else {}
        start("compositor", [args.labwc, "-C", str(config)], compositor_env)
        wait("Wayland socket", lambda: (runtime / "wayland-0").exists())
        start("portal-backend", [str(service_binary("xdg-desktop-portal-wlr")), "-l", "DEBUG"])
        start("portal", [str(service_binary("xdg-desktop-portal"))])
        time.sleep(1.0)
        probe = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop", "--method",
             "org.freedesktop.DBus.Properties.Get", "org.freedesktop.portal.ScreenCast",
             "AvailableSourceTypes"], env=env, capture_output=True, text=True, timeout=40)
        report["source_types"] = (probe.stdout + probe.stderr).strip()
        check("wlr ScreenCast offers window sources", "uint32" in probe.stdout,
              report["source_types"])

        (folder / "color").write_text(SLIDE_A)
        start("slide", [sys.executable, script, "--slide", str(folder)])
        wait("synthetic slide", lambda: (folder / "slide-ready").exists())
        open_pointer()
        app_proc = start("app", [str(app)])

        def dbcount(sql):
            with sqlite3.connect(data / "studio.sqlite3") as db:
                return db.execute(sql).fetchone()[0]

        def frames():
            return sorted(data.glob("frames/*/screen-*.jpg"),
                          key=lambda p: p.stat().st_mtime_ns)

        wait("meeting database", lambda: dbcount("select count(*) from meetings") >= 0)

        def focus():
            subprocess.run([args.wtype, "-k", "F12"], env=env, timeout=20, check=True)
            return (folder / "workbench-mapped").exists()

        wait("mapped workbench window", focus)
        time.sleep(1.5)
        check("workbench mapped on the private Wayland seat", app_proc.poll() is None, None)

        shortcut("ctrl+n")
        type_text("MYS-3030 wlroots regression")
        shortcut("ctrl+s")
        wait("created meeting",
             lambda: dbcount("select count(*) from meetings where title="
                             "'MYS-3030 wlroots regression'"), 30)
        check("created a meeting through the GUI", True, None)
        time.sleep(1.2)

        def capture(index, expecting, tag):
            path = wait(f"frame {index + 1} ({tag})",
                        lambda: len(frames()) >= index + 1 and frames()[index], 90)
            with Image.open(path) as image:
                image.load()
                rgb = image.convert("RGB")
                pixel = rgb.getpixel((100, 100))
            info = {"index": index + 1, "tag": tag, "file": path.name,
                    "size": list(image.size), "pixel": list(pixel),
                    "jpeg_bytes": path.stat().st_size,
                    "sha256": subprocess.run(["sha256sum", str(path)], capture_output=True,
                                             text=True).stdout.split()[0]}
            ok = tuple(info["size"]) == SIZE and \
                max(abs(a - b) for a, b in zip(pixel, expecting)) <= PIXEL_TOLERANCE
            check(f"frame {info['index']} ({tag}) is the window at 640x460 in the "
                  "expected pattern", ok, json.dumps(info, ensure_ascii=False))
            report.setdefault("frames", []).append(info)
            return ok, info

        def preview(name):
            path = shot(name)
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                # The centred 710x520 会议画面 dialog paints the frame at its top.
                return rgb.crop((310, 190, 690, 450)).resize((1, 1)).getpixel((0, 0))

        def preview_is(name, expected, seconds=30):
            def painted():
                value = preview(name)
                return value if max(abs(a - b) for a, b in zip(value, expected)) \
                    <= PIXEL_TOLERANCE * 3 else None
            return wait(f"preview matching {list(expected)}", painted, seconds)

        def capture_menu(item):
            """Pick a 窗口画面 row: 0 预览, 1 重新截图, 2 更换, 3 停止."""
            click(*CAPTURE_MENU)
            for _ in range(item):
                key("-k", "Down")
            key("-k", "Return")
            time.sleep(0.8)

        # leg 1: capture the selected window with pattern A
        (folder / "selection").unlink(missing_ok=True)
        shortcut("ctrl+g")
        wait("window picker response", lambda: (folder / "selection").is_file(), 120)
        selected = (folder / "selection").read_text().strip()
        offered = json.loads((folder / "chooser-rows").read_text().splitlines()[0])
        check("the portal offered the target window and the picker chose it",
              TITLE in selected, f"selected={selected!r} offered={offered}")
        ok_a, first = capture(0, RGB_A, "first frame")
        sample_a = preview_is("01-first-frame-gui", RGB_A)
        check("workbench preview shows pattern A", sample_a is not None,
              f"preview sample={sample_a}")

        # leg 2: B in the same window, then 重新截图 from the toolbar menu
        (folder / "color").write_text(SLIDE_B)
        time.sleep(1.0)
        key("-k", "Escape")
        time.sleep(0.5)
        capture_menu(1)
        ok_b, second = capture(1, RGB_B, "refreshed frame")
        capture_menu(0)
        sample_b = preview_is("02-refreshed-gui", RGB_B)
        check("A/B refresh delivered pattern B in a new frame and in the preview",
              ok_a and ok_b and sample_b is not None and
              first.get("sha256") != second.get("sha256"),
              f"frame1={first.get('pixel')} frame2={second.get('pixel')} "
              f"preview_before={sample_a} preview_after={sample_b}")
        key("-k", "Escape")
        time.sleep(0.6)

        # leg 3: stop, then reselect the same window
        capture_menu(3)
        released = "session closed" in (folder / "portal-backend.log").read_text()
        shot("03-after-stop")
        check("stopping releases the wlr portal session", released,
              "portal-backend log reports the session closed")
        (folder / "selection").unlink(missing_ok=True)
        shortcut("ctrl+g")
        wait("second window picker response", lambda: (folder / "selection").is_file(), 120)
        reselected = (folder / "selection").read_text().strip()
        check("re-selecting lists and chooses the target window again",
              TITLE in reselected, f"selected={reselected!r}")
        ok_c, third = capture(2, RGB_B, "reselect frame")
        shot("04-reselect-frame")
        key("-k", "Escape")
        time.sleep(0.5)
        capture_menu(3)
        check("second stop released the portal session",
              "session closed" in (folder / "portal-backend.log").read_text())
        check("the application survived the whole capture cycle",
              app_proc.poll() is None, f"rc={app_proc.poll()}")
        report["ok"] = ok_a and ok_b and ok_c and \
            all(entry["ok"] for entry in report["steps"])
        report["app_log"] = (folder / "app.log").read_text(errors="replace")[-4000:]

    report["ok"] = all(entry["ok"] for entry in report["steps"])
    report["namespace"]["runtime_dir"] = str(runtime)
    shutil.rmtree(runtime, ignore_errors=True)
    (args.out / f"{args.label}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"[{'PASS' if report['ok'] else 'FAIL'}] {args.label} overall", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
