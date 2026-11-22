"""Drive a release on an isolated X display or a native Wayland window in nested Weston."""

import argparse
import contextlib
import ctypes as ct
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time


class Surface:
    """Read narrow pixel strips directly from the displayed window without pausing input."""

    def __init__(self, window):
        lib = self.x = ct.CDLL("libX11.so.6")
        for name, args, result in [
            ("XOpenDisplay", [ct.c_char_p], ct.c_void_p),
            (
                "XGetImage",
                [
                    ct.c_void_p,
                    ct.c_ulong,
                    ct.c_int,
                    ct.c_int,
                    ct.c_uint,
                    ct.c_uint,
                    ct.c_ulong,
                    ct.c_int,
                ],
                ct.c_void_p,
            ),
            ("XGetPixel", [ct.c_void_p, ct.c_int, ct.c_int], ct.c_ulong),
            ("XDestroyImage", [ct.c_void_p], ct.c_int),
            ("XCloseDisplay", [ct.c_void_p], ct.c_int),
        ]:
            f = getattr(lib, name)
            f.argtypes, f.restype = args, result
        self.display = lib.XOpenDisplay(None)
        assert self.display
        self.window = int(window)

    def strip(self, x, y, w, h):
        image = self.x.XGetImage(self.display, self.window, x, y, w, h, 0xFFFFFF, 2)
        assert image
        try:
            return [self.x.XGetPixel(image, i, j) for j in range(h) for i in range(w)]
        finally:
            self.x.XDestroyImage(image)


def color(values):
    return (values[0] << 16) | (values[1] << 8) | values[2]


def until(desc, test, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(desc)


def run(app, protocol, work):
    env = dict(
        os.environ,
        XDG_CONFIG_HOME=str(work / "config"),
        XDG_DATA_HOME=str(work / "data"),
        LIBGL_ALWAYS_SOFTWARE="1",
        XDG_SESSION_TYPE=protocol,
    )
    env.pop("WAYLAND_DISPLAY", None)
    host_env = dict(env, XDG_SESSION_TYPE="x11")
    with contextlib.ExitStack() as stack:

        def launch(*args):
            p = subprocess.Popen(
                args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            def close():
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=8)

            stack.callback(close)
            return p

        def x(*args):
            return subprocess.check_output(
                ["xdotool", *map(str, args)], env=host_env, text=True, timeout=10
            ).strip()

        if protocol == "x11":
            launch("openbox")
            until(
                "window manager",
                lambda: b"_NET_SUPPORTING_WM_CHECK(WINDOW)"
                in subprocess.check_output(
                    ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], env=env
                ),
            )
        if protocol == "wayland":
            runtime = work / "runtime"
            runtime.mkdir(mode=0o700)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["XDG_CURRENT_DESKTOP"] = "weston"
            launch(
                "weston",
                "--backend=x11",
                "--renderer=pixman",
                "--shell=kiosk-shell.so",
                "--no-config",
                "--socket=test-wayland",
                "--width=1250",
                "--height=800",
            )
            until(
                "nested compositor socket", lambda: (runtime / "test-wayland").exists()
            )
            env["WAYLAND_DISPLAY"] = "test-wayland"

        if protocol == "wayland":
            import re

            def compositor_window():
                tree = subprocess.check_output(
                    ["xwininfo", "-root", "-tree"], env=host_env, text=True
                )
                m = re.search(r'(0x[0-9a-f]+) "Weston Compositor - screen0"', tree)
                return str(int(m[1], 16)) if m else None

            win = until("nested output", compositor_window)
            # Weston maps its kiosk output on its first presentation.
            app_process = launch(app, "--wayland")
            x("windowmap", win)
            until(
                "mapped nested desktop",
                lambda: "IsViewable"
                in subprocess.check_output(
                    ["xwininfo", "-id", win], env=host_env, text=True
                ),
                15,
            )
        else:
            app_process = launch(app, "--x11")
            win = x(
                "search", "--sync", "--onlyvisible", "--class", "^tingsu$"
            ).splitlines()[-1]
        if protocol == "x11":
            x("windowsize", win, 1250, 800)
        x("windowfocus" if protocol == "wayland" else "windowactivate", "--sync", win)
        surface = Surface(win)
        stack.callback(surface.x.XCloseDisplay, surface.display)
        theme = json.loads(
            subprocess.check_output(
                [app, "--read-system-appearance"], env=env, timeout=8
            )
        )

        def rgb(role):
            return [theme[role][channel] for channel in "RGB"]

        border, panel = color(rgb("Border")), color(rgb("Panel"))
        thumb = color(
            [(x * 78 + y * 22) // 100 for x, y in zip(rgb("Text"), rgb("Window"))]
        )

        def handle():
            a = surface.strip(570, 190, 580, 1)
            b = surface.strip(570, 535, 580, 1)
            hits = [570 + i for i, p in enumerate(a) if p == border and b[i] == border]
            return hits[0] if len(hits) == 1 else None

        start = until("board divider", handle, 16)

        def key(k):
            x("key", "--clearmodifiers", k)
            time.sleep(0.22)

        # Create an editable meeting, then use SQLite only to supply synthetic
        # inputs. All scrolling and resizing exercise the normal GUI.
        key("ctrl+n")
        x("type", "Scroll QA")
        key("ctrl+s")
        db = sqlite3.connect(work / "data/tingsu/studio.sqlite3")
        stack.callback(db.close)
        mid = until(
            "meeting saved",
            lambda: db.execute(
                "select id from meetings where title='Scroll QA'"
            ).fetchone(),
        )[0]

        def append(begin, count):
            for i in range(begin, begin + count):
                sentence = f"Record {i:04} with meeting text. "
                db.execute(
                    "insert into events(meeting,kind,at,end,speaker,text) values(?,?,?,?,?,?)",
                    (mid, "speech", i, i + 1, "A", sentence * (1 + (i % 9) * 3)),
                )
            db.commit()
            key("ctrl+l")
            key("Escape")

        append(0, 180)
        sx = start - 27

        def bounds():
            bar = surface.strip(sx - 3, 90, 1, 600)
            try:
                t = bar.index(thumb)
            except ValueError:
                return None
            b = t
            while b + 1 < len(bar) and bar[b + 1] == thumb:
                b += 1
            if b - t < 10:
                return None
            top = t
            while top and bar[top - 1] == panel:
                top -= 1
            end = b
            while end + 1 < len(bar) and bar[end + 1] == panel:
                end += 1
            if end - top < 180:
                return None
            return top + 90, end + 90, t + 90, b + 90

        def bottom():
            r = bounds()
            return r if r and r[1] - r[3] <= 1 else None

        top, end, t, b = until("new meeting followed the latest record", bottom)
        # Reopen the selected long meeting. Its very first viewport should show the tail.
        app_process.terminate()
        app_process.wait(timeout=8)
        launch(app, "--" + protocol)
        if protocol == "x11":
            win = x(
                "search", "--sync", "--onlyvisible", "--class", "^tingsu$"
            ).splitlines()[-1]
            x("windowsize", win, 1250, 800)
            x("windowactivate", "--sync", win)
            surface.window = int(win)
        until("default after reopen", bottom, 15)
        content = lambda: surface.strip(250, end - 40, 455, 30)
        tail = content()
        append(180, 3)
        until("new content at bottom", lambda: bottom() and content() != tail)
        top, end, t, b = bottom()
        middle = (top + end) // 2
        x("mousemove", "--window", win, sx + 5, middle, "click", 1)

        def at(y):
            r = bounds()
            return r and abs((r[2] + r[3]) // 2 - y) < 7

        until("track press at cursor", lambda: at(middle))
        x("mousemove", "--window", win, sx + 5, middle, "mousedown", 1)
        for y in (middle - 33, middle - 78, middle + 59, middle):
            x("mousemove", "--window", win, sx + 5, y)
            until(f"thumb under pressed pointer {y}", lambda: at(y))
        x("mouseup", 1)
        time.sleep(0.12)
        reading = content()
        append(183, 3)
        until("snapshot refresh", lambda: bounds())
        assert content() == reading, "Appending records moved the reading anchor"
        x(
            "mousemove",
            "--window",
            win,
            sx + 5,
            middle,
            "mousedown",
            1,
            "mousemove",
            "--window",
            win,
            sx + 5,
            end + 15,
            "mouseup",
            1,
        )
        until("dragging to end enables following", bottom)
        tail = content()
        append(186, 3)
        until("following resumed", lambda: bottom() and content() != tail)

        # Read intermediate painted positions while the mouse keeps moving.
        x("mousemove", "--window", win, start, 380, "mousedown", 1)
        samples = []
        for target in [start - i for i in range(0, 121, 3)] + [
            start - 120 + i for i in range(0, 121, 3)
        ]:
            x("mousemove", "--window", win, target, 380)
            # Feed one motion per display refresh without waiting for the app.
            time.sleep(1 / 60)
            actual = handle()
            if actual is not None:
                samples.append((target, actual))
        x("mouseup", 1)
        assert len(set(v for _, v in samples)) >= 20, samples
        assert (
            sorted(abs(v - y) for y, v in samples)[len(samples) * 9 // 10] <= 15
        ), samples
        print(
            protocol,
            "passed follow, reopen, rolling history, track and thumb. Continuous divider drag:",
            len(set(v for _, v in samples)),
            "distinct painted positions;",
            len(samples),
            "samples.",
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--protocol", choices=("wayland", "x11"), required=True)
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as d:
        run(args.app.resolve(), args.protocol, Path(d))
