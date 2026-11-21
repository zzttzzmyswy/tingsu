"""Run the release application's mouse regression on a dedicated 1440x950 X display."""

import argparse
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from PIL import Image


def run(app, work):
    env = dict(
        os.environ,
        XDG_CONFIG_HOME=str(work / "config"),
        XDG_DATA_HOME=str(work / "data"),
        XDG_SESSION_TYPE="x11",
        LIBGL_ALWAYS_SOFTWARE="1",
    )
    env.pop("WAYLAND_DISPLAY", None)
    wm = subprocess.Popen(
        ["openbox"], env=env, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
    )
    try:
        proc = subprocess.Popen([app, "--x11"], env=env)
        try:

            def x(*args):
                return subprocess.check_output(
                    ["xdotool", *map(str, args)], env=env, timeout=12, text=True
                )

            win = x(
                "search", "--sync", "--onlyvisible", "--class", "^tingsu$"
            ).splitlines()[-1]
            x("windowsize", win, 1250, 800)
            x("windowactivate", "--sync", win)
            time.sleep(1)

            def click(px, py):
                x("mousemove", "--window", win, px, py, "click", 1)
                time.sleep(0.18)

            def board_divider():
                buf = subprocess.check_output(
                    ["import", "-window", win, "png:-"], env=env, timeout=12
                )
                pixels = Image.open(BytesIO(buf)).convert("RGB")
                color = (230, 236, 236)
                matches = [
                    px
                    for px in range(350, 1200)
                    if pixels.getpixel((px, 148)) == pixels.getpixel((px, 570)) == color
                ]
                assert matches, "Board divider missing"
                return sum(matches) // len(matches)

            start = board_divider()
            x("mousemove", "--window", win, start, 380, "mousedown", 1)
            try:
                for delta in (45, 100, 165, 70):
                    target = start - delta
                    x("mousemove", "--window", win, target, 380)
                    time.sleep(0.15)
                    actual = board_divider()
                    assert abs(actual - target) < 4, (target, actual)
            finally:
                x("mouseup", 1)
            with sqlite3.connect(work / "data/tingsu/studio.sqlite3") as db:
                for index in range(5):
                    click(112, 18)
                    click(140, 55)
                    x("type", f"Mouse QA {index}")
                    x("key", "ctrl+s")
                    time.sleep(0.35)
                assert db.execute(
                    "select count(*) from meetings where title like 'Mouse QA %'"
                ).fetchone() == (5,)
                # The first click outside the open menu opens the note editor.
                click(112, 18)
                click(1210, 120)
                x("type", "Click through")
                x("key", "ctrl+s")
                time.sleep(0.4)
                assert db.execute(
                    "select count(*) from notes where title='Click through'"
                ).fetchone() == (1,)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    finally:
        wm.terminate()
        wm.wait(timeout=10)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as work:
        run(args.app.resolve(), Path(work))
    print("Mouse menus, outside click and four live divider positions passed.")
