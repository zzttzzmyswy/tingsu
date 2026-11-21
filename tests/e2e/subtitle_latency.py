"""Measure capture click to the first rendered nonempty speech row in the real desktop."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

from desktop import Page, debug_environment


def measure(app, recording, backend, provider):
    with tempfile.TemporaryDirectory(prefix="tingsu-first-caption-") as temp:
        root = Path(temp)
        env = os.environ.copy()
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            folder = root / key
            folder.mkdir()
            env[key] = str(folder)
        cfg = root / "XDG_CONFIG_HOME/tingsu"
        cfg.mkdir()
        (cfg / "settings.json").write_text(json.dumps({"provider": provider}))
        env["TINGSU_APP_DIR"] = str(recording.parent.parent)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env.update(debug_environment(port))
        with subprocess.Popen([app, "--" + backend], env=env) as process:
            try:
                p = Page(port)
                try:
                    p.until("S.media?.some(m=>m.path===" + json.dumps(str(recording)) + ")")
                    p.click("library")
                    p.click("media", recording.stem)
                    p.field("#meeting-name", "首条字幕测试")
                    # Listen inside the renderer so inspector polling and IPC are excluded.
                    p.js("""document.addEventListener('click', e => {
                      if (e.target.closest('[data-action="create-start"]')) window.captionStart=performance.now();
                    }, true);
                    new MutationObserver((_, o) => {
                      if (!window.captionStart || !S.meeting || S.meeting.title!=='首条字幕测试') return;
                      const row = document.querySelector('#feed-list .event-speech .event-copy');
                      if (row?.innerText.trim() && row.getClientRects().length) {
                        o.disconnect(); requestAnimationFrame(()=>requestAnimationFrame(()=>{
                          window.captionElapsed=performance.now()-window.captionStart;
                        }));
                      }
                    }).observe(document.body,{subtree:true,childList:true,characterData:true});true""")
                    p.click("create-start")
                    elapsed = p.until("window.captionElapsed", 120)
                    return round(elapsed / 1000, 3)
                finally:
                    p.close()
            finally:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--app", required=True, type=Path)
    a.add_argument("--recording", required=True, type=Path, help="a file under an isolated videos/ directory")
    a.add_argument("--backend", choices=["x11", "wayland"], default="x11")
    a.add_argument("--provider", choices=["siliconflow", "sophnet"], default="siliconflow")
    a.add_argument("--repeat", type=int, default=3)
    args = a.parse_args()
    for index in range(args.repeat):
        elapsed = measure(args.app.resolve(), args.recording.resolve(), args.backend, args.provider)
        print(json.dumps({"run": index+1, "first_caption_seconds": elapsed}), flush=True)
