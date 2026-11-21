"""Run with xvfb-run -a dbus-run-session -- python3 tests/e2e/linux_streams.py --app <AppImage>.

Set SILICONFLOW_API_KEY for the live recognition leg. The two speech sources play
via FFmpeg with the flite filter on an isolated audio server's virtual sink. No physical desktop audio is used.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
from contextlib import ExitStack

from desktop import debug_environment, Page, wait


def run(app, key, tts_ffmpeg):
    phrases = {
        "Lighthouse": "The blue lighthouse stands beside the ocean. The blue lighthouse stands beside the ocean.",
        "Elephants": "Seven purple elephants are eating bananas in the forest. Seven purple elephants are eating bananas in the forest.",
    }
    with tempfile.TemporaryDirectory(prefix="tingsu-stream-acceptance-") as td, ExitStack() as stack:
        base = Path(td)
        env = dict(os.environ)
        for keyname in (
            "PIPEWIRE_REMOTE",
            "PIPEWIRE_RUNTIME_DIR",
            "PULSE_SERVER",
            "PULSE_RUNTIME_PATH",
            "PULSE_SINK",
            "PULSE_SOURCE",
            "WAYLAND_DISPLAY",
            "OPENAI_API_KEY",
            "SOPHNET_API_KEY",
            "SILICONFLOW_API_KEY",
            "SESSION_MANAGER",
        ):
            env.pop(keyname, None)
        for keyname, dirname in (
            ("XDG_RUNTIME_DIR", "runtime"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_STATE_HOME", "state"),
            ("HOME", "home"),
        ):
            path = base / dirname
            path.mkdir(mode=0o700)
            env[keyname] = str(path)
        env.update(
            XDG_SESSION_TYPE="x11",
            QT_QPA_PLATFORM="xcb",
            XDG_CURRENT_DESKTOP="TINGSU_TEST",
            PULSE_SERVER="unix:" + str(base / "runtime/pulse/native"),
        )

        def start(name, argv, overrides=None):
            log = stack.enter_context((base / (name + ".log")).open("wb"))
            proc = subprocess.Popen(
                argv,
                stdout=log,
                stderr=log,
                env={**env, **(overrides or {})},
                cwd=base,
                start_new_session=True,
            )

            def cleanup():
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(4)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(3)

            stack.callback(cleanup)
            return proc

        def command(argv):
            return subprocess.check_output(
                argv, env=env, text=True, stderr=subprocess.DEVNULL, timeout=10
            )

        start("pipewire", ["pipewire"])
        wait(lambda: (base / "runtime/pipewire-0").exists(), "private PipeWire")
        start("policy", ["wireplumber", "-p", "policy"])
        start("pulse", ["pipewire-pulse"])
        wait(lambda: (base / "runtime/pulse/native").exists(), "private Pulse socket")
        command(["pactl", "load-module", "module-null-sink", "sink_name=tingsu_null"])
        command(["pactl", "set-default-sink", "tingsu_null"])
        for name, text in phrases.items():
            audio = base / (name + ".wav")
            voice = "slt" if name == "Lighthouse" else "rms"
            subprocess.run(
                [tts_ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"flite=text={text}:voice={voice}",
                 "-ar", "16000", "-ac", "1", str(audio)],
                check=True,
            )
            start(
                name,
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-re",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(audio),
                    "-f",
                    "pulse",
                    "-name",
                    name,
                    "-stream_name",
                    name,
                    "-device",
                    "tingsu_null",
                    name,
                ],
            )
        wait(
            lambda: len(command(["pactl", "list", "short", "sink-inputs"]).strip().splitlines()) == 2,
            "two concurrent synthetic applications",
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app_proc = start(
            "app",
            [str(app), "--x11"],
            {"SILICONFLOW_API_KEY": key, **debug_environment(port)},
        )
        page = Page(port)
        stack.callback(page.close)
        for backend, target, distractor in (
            ("pipewire-app", "Lighthouse", "Elephants"),
            ("pulse-app", "Elephants", "Lighthouse"),
        ):
            page.click("new")
            page.until(
                "S.sources.some(s=>s.kind==="
                + json.dumps(backend)
                + " && s.label.includes("
                + json.dumps(target)
                + "))"
            )
            source = page.js(
                "S.sources.find(s=>s.kind==="
                + json.dumps(backend)
                + " && s.label.includes("
                + json.dumps(target)
                + ")).id"
            )
            page.field("#meeting-name", backend + " · " + target)
            page.field("#source-select", source)
            page.click("create-start")
            page.until("S.capture==='recording'")
            page.until("S.events.some(e=>e.kind==='speech')", 85)
            page.click("stop")
            page.until("S.capture==='idle'", 85)
            texts = page.js("S.events.filter(e=>e.kind==='speech').map(e=>e.text)")
            actual = " ".join(texts).casefold()
            target_words = (
                ("lighthouse", "blue", "ocean")
                if target == "Lighthouse"
                else ("elephant", "banana", "forest")
            )
            other_words = (
                ("elephant", "banana", "purple", "forest")
                if distractor == "Elephants"
                else ("lighthouse", "ocean", "blue")
            )
            assert sum(word in actual for word in target_words) >= 2, (backend, target, texts)
            assert not any(word in actual for word in other_words), (backend, distractor, texts)
            assert page.js("S.pending===0")
            print(
                json.dumps(
                    {
                        "backend": backend,
                        "target": target,
                        "concurrent_excluded": distractor,
                        "transcript": texts,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        assert app_proc.poll() is None


if __name__ == "__main__":
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--app", type=Path, required=True)
    arguments.add_argument("--tts-ffmpeg", default="ffmpeg", help="FFmpeg binary with the flite filter")
    parsed = arguments.parse_args()
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise SystemExit("Set SILICONFLOW_API_KEY for this online acceptance test.")
    run(parsed.app.resolve(), key, parsed.tts_ffmpeg)
