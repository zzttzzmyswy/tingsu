"""Exercise the packaged Gio preferences with an installed Codex on an isolated X display.

Run with xvfb-run -a -s '-screen 0 1440x1000x24'. Supply model IDs offered by the
installed CLI. This test reads its catalog, without starting an agent conversation.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def run(app, models):
    with tempfile.TemporaryDirectory() as work:
        env = dict(
            os.environ,
            XDG_CONFIG_HOME=work + "/config",
            XDG_DATA_HOME=work + "/data",
            XDG_SESSION_TYPE="x11",
            LIBGL_ALWAYS_SOFTWARE="1",
        )
        env.pop("WAYLAND_DISPLAY", None)
        settings = Path(work, "config", "tingsu", "settings.json")
        wm = subprocess.Popen(
            ["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            for index, model in enumerate(models):
                process = subprocess.Popen([app, "--x11"], env=env)
                try:

                    def x(*args):
                        return subprocess.check_output(
                            ["xdotool", *args], env=env, timeout=12, text=True
                        )

                    win = x(
                        "search", "--sync", "--onlyvisible", "--class", "^tingsu$"
                    ).splitlines()[-1]
                    x("windowsize", win, "1250", "800")
                    x("windowactivate", "--sync", win)
                    time.sleep(1)

                    def click(a, b):
                        x("mousemove", "--window", win, str(a), str(b), "click", "1")
                        time.sleep(0.3)

                    def press(k):
                        x("key", "--clearmodifiers", k)
                        time.sleep(0.3)

                    # Open via the desktop menu, then select the Local Agent category.
                    if index == 0:
                        click(112, 18)  # File
                        click(260, 18)  # Switch directly to Settings
                        click(290, 55)  # Preferences
                    else:
                        press("alt+s")
                        press("Return")
                    click(300, 275)
                    click(710, 395)
                    x("type", "--clearmodifiers", "--delay", "20", model)
                    # The searchable menu stays open while the CLI is loading.
                    # First accept the matching row, then save preferences.
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline:
                        press("ctrl+s")
                        press("ctrl+s")
                        if settings.exists():
                            data = json.loads(settings.read_text())
                            if (
                                data.get("local_agent_options", {})
                                .get("codex", {})
                                .get("model")
                                == model
                            ):
                                break
                    else:
                        raise AssertionError("Could not select and save " + model)
                    assert data["provider"] == "siliconflow"
                    print("Selected and persisted", model, flush=True)
                finally:
                    process.terminate()
                    process.wait(timeout=8)
        finally:
            wm.terminate()
            wm.wait(timeout=8)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True, type=Path)
    p.add_argument("--model", action="append", required=True)
    args = p.parse_args()
    run(args.app.resolve(), args.model)
