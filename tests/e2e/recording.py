"""Verify a user-provided recording and absolute speaker timestamps through the released Linux GUI.

Run in an isolated X11 session with a window manager. This test submits the
selected excerpt to SiliconFlow. It prints aggregate counts, not meeting text.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from desktop import debug_environment, Page, native_file


def verify(app, recording, offset, seconds, min_speakers):
    with tempfile.TemporaryDirectory(prefix="tingsu-recording-") as folder:
        base = Path(folder)
        short_path = base / ("recording-check" + recording.suffix)
        short_path.symlink_to(recording)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        for var in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            path = base / var
            path.mkdir(mode=0o700)
            env[var] = str(path)
        env.update(debug_environment(port))
        with subprocess.Popen([str(app), "--x11"], cwd=base, env=env) as proc:
            try:
                p = Page(port)
                try:
                    p.click("library")
                    p.click("import")
                    native_file(short_path)
                    media = p.until(
                        "S.media.find(m=>"
                        + json.dumps([str(short_path), str(recording)])
                        + ".includes(m.path))?.title"
                    )
                    p.click("media", media)
                    p.field("#meeting-name", "录屏片段验收")
                    p.field("#offset", str(offset))
                    p.click("create-start")
                    p.until("S.capture==='recording'")
                    time.sleep(seconds)
                    p.click("stop")
                    p.until("S.capture==='idle' && S.pending===0", 120)
                    data = p.js("S.events.filter(e=>e.kind==='speech').map(e=>[e.at,e.end,e.speaker])")
                    speakers = set(item[2] for item in data)
                    assert data and len(speakers) >= min_speakers, (len(data), len(speakers))
                    assert all(offset <= start <= end <= offset + seconds + 5 for start, end, _ in data)
                    print(json.dumps({"segments": len(data), "speakers": len(speakers),
                                      "first": min(r[0] for r in data), "last": max(r[1] for r in data)}))
                finally:
                    p.close()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, required=True)
    cli.add_argument("--recording", type=Path, required=True)
    cli.add_argument("--offset", type=int, default=300)
    cli.add_argument("--seconds", type=int, default=36)
    cli.add_argument("--min-speakers", type=int, default=2)
    args = cli.parse_args()
    if not os.environ.get("SILICONFLOW_API_KEY"):
        cli.error("Set SILICONFLOW_API_KEY before running the online test.")
    verify(args.app.resolve(), args.recording.resolve(strict=True), args.offset, args.seconds, args.min_speakers)
