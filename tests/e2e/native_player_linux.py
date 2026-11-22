"""Exercise an actual Linux release, an external mpv and a caller-supplied recording.

The isolated desktop has a private PipeWire server and a local ASR endpoint.
No recording or captured data leaves the machine. Pass an empty --output directory.
"""

import argparse
import contextlib
import email
import email.policy
import http.server
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import wave
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageOps
from scipy.signal import correlate


def wait(label, fn, seconds=32):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, sqlite3.Error, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(0.15)
    raise RuntimeError(f"Timed out waiting for {label}")


def validate_audio(recording, folder):
    with tempfile.TemporaryDirectory() as temp:
        pcm = Path(temp) / "reference.pcm"
        subprocess.run(
            [
                "mpv",
                "--no-config",
                "--no-terminal",
                "--no-video",
                "--start=299",
                "--length=95",
                "--audio-format=s16",
                "--audio-channels=mono",
                "--audio-samplerate=16000",
                "--ao=pcm",
                "--ao-pcm-waveheader=no",
                "--ao-pcm-file=" + str(pcm),
                "--",
                recording,
            ],
            check=True,
            timeout=90,
        )
        ref = np.fromfile(pcm, dtype="<i2").astype(float)
    best = 0
    for name in sorted(folder.glob("audio-*.wav")):
        with wave.open(str(name), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (
                16000,
                1,
                2,
            )
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(
                float
            )
        for segment in np.array_split(audio, max(1, len(audio) // 16000)):
            if len(segment) < 8000 or np.std(segment) < 50:
                continue
            segment -= segment.mean()
            corr = correlate(ref, segment, mode="valid", method="fft")
            energy = np.r_[0.0, np.cumsum(ref**2)]
            denom = np.sqrt(
                np.maximum(1.0, energy[len(segment) :] - energy[: -len(segment)])
                * np.sum(segment**2)
            )
            best = max(best, np.max(corr / denom))
    assert (
        best > 0.70
    ), f"Player audio differs from the recording: correlation {best:.3f}"
    return round(float(best), 4)


def compare_frame(received, ref):
    def pixels(path):
        return np.asarray(
            ImageOps.fit(Image.open(path).convert("L"), (128, 72)), dtype=float
        ).ravel()

    a, b = pixels(received), pixels(ref)
    return float(np.corrcoef(a, b)[0, 1])


def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    run_dir = root / "runtime"
    run_dir.mkdir(mode=0o700)
    env = os.environ.copy()
    for k in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "WAYLAND_SOCKET",
        "DBUS_SESSION_BUS_ADDRESS",
        "PULSE_SERVER",
        "PIPEWIRE_REMOTE",
        "PIPEWIRE_RUNTIME_DIR",
        "PULSE_RUNTIME_PATH",
        "SESSION_MANAGER",
        "XAUTHORITY",
        "KDE_FULL_SESSION",
        "QT_QPA_PLATFORM",
        "QT_PLUGIN_PATH",
    ):
        env.pop(k, None)
    for k in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    ):
        d = root / k.lower()
        d.mkdir(mode=0o700)
        env[k] = str(d)
    wl = args.protocol == "wayland"
    title = "Tingsu QA External Player"
    env.update(
        XDG_RUNTIME_DIR=str(run_dir),
        XDG_SESSION_TYPE=args.protocol,
        XDG_CURRENT_DESKTOP="wlr" if wl else "Openbox",
        LIBGL_ALWAYS_SOFTWARE="1",
        SILICONFLOW_API_KEY="local-fixture",
        LANG="C.UTF-8",
    )
    app = args.app.resolve()
    video = args.recording.resolve()

    def cmd(*a, **kw):
        return subprocess.check_output(
            a, env=env, timeout=12, stderr=subprocess.DEVNULL, **kw
        )

    def start(label, argv, patch=None):
        log = stack.enter_context(open(root / (label + ".log"), "wb"))
        p = subprocess.Popen(argv, env=env | (patch or {}), stdout=log, stderr=log)

        def stop():
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(4)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()

        stack.callback(stop)
        return p

    def press(k):
        if wl:
            opts = (
                ["-M", "ctrl", "-k", k.split("+")[-1], "-m", "ctrl"]
                if "+" in k
                else ["-k", k]
            )
            cmd(args.wtype, *opts)
        else:
            cmd("xdotool", "key", "--clearmodifiers", k)
        time.sleep(0.4)

    def type_text(s):
        if wl:
            cmd(args.wtype, "--", s)
        else:
            cmd("xdotool", "type", "--clearmodifiers", "--delay", "15", s)
        time.sleep(0.15)

    def player(command):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(str(root / "mpv.sock"))
            s.sendall(json.dumps({"command": command}).encode() + b"\n")
            f = s.makefile("rb")
            for line in f:
                r = json.loads(line)
                if "error" in r:
                    assert r["error"] == "success", r
                    return r.get("data")

    def dbcount(sql, args=()):
        with sqlite3.connect(
            Path(env["XDG_DATA_HOME"]) / "tingsu/studio.sqlite3"
        ) as db:
            return db.execute(sql, args).fetchone()[0]

    def screen(path):
        if wl:
            cmd("grim", str(path))
        else:
            cmd("import", "-window", "root", str(path))

    def toolbar_pixels():
        path = root / "meter.png"
        screen(path)
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"))

    def idle_meter():
        pixels = toolbar_pixels()
        idle = (pixels == border).all(axis=2)
        for y, row in enumerate(idle):
            # Ten inactive segments separated by one physical pixel at 1x.
            match = re.search(rb"(?:\x01{2,10}\x00{1,3}){9}\x01{2,10}", row.tobytes())
            if match:
                return y + 2, match.start(), match.end()
        return None

    class Listen(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            typ = self.headers["Content-Type"]
            message = email.message_from_bytes(
                ("Content-Type: " + typ + "\r\n\r\n").encode()
                + self.rfile.read(int(self.headers["Content-Length"])),
                policy=email.policy.default,
            )
            file = next(
                p
                for p in message.iter_parts()
                if p.get_param("name", header="content-disposition") == "file"
            )
            (root / f"audio-{time.monotonic_ns()}.wav").write_bytes(
                file.get_payload(decode=True)
            )
            data = b'{"text":"Captured from player","segments":[{"text":"Captured from player","start":0,"end":2,"speaker":"A"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    with contextlib.ExitStack() as stack:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Listen)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        env["TINGSU_SILICONFLOW_URL"] = f"http://127.0.0.1:{server.server_port}/asr"
        bus = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        stack.callback(lambda: (bus.terminate(), bus.wait()))
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().strip()
        config = Path(env["XDG_CONFIG_HOME"])
        for s in ("xdg-desktop-portal", "xdg-desktop-portal-wlr"):
            (config / s).mkdir()
        (config / "xdg-desktop-portal/portals.conf").write_text(
            "[preferred]\ndefault=none\norg.freedesktop.impl.portal.ScreenCast=wlr\n"
        )
        picker = root / "choose-window"
        picker.write_text(
            "#!/bin/sh\ntee "
            + shlex.quote(str(root / "portal-offered"))
            + " | grep -F -m1 "
            + shlex.quote(title)
            + "\n"
        )
        picker.chmod(0o755)
        (config / "xdg-desktop-portal-wlr/config").write_text(
            f"[screencast]\nchooser_type=dmenu\nchooser_cmd={picker}\nmax_fps=10\n"
        )
        if wl:
            # The service may initialize its database before its Wayland window
            # is mapped. Focus that specific top-level before sending UI input.
            rc = ET.Element("labwc_config")
            bind = ET.SubElement(ET.SubElement(rc, "keyboard"), "keybind", key="F12")
            each = ET.SubElement(bind, "action", name="ForEach")
            ET.SubElement(ET.SubElement(each, "query"), "title").text = "*会议工作台*"
            actions = ET.SubElement(each, "then")
            ET.SubElement(actions, "action", name="Focus")
            ET.SubElement(actions, "action", name="Raise")
            focused = root / "workbench-mapped"
            ET.SubElement(
                actions,
                "action",
                name="Execute",
                command=shlex.join(["touch", str(focused)]),
            )
            ET.ElementTree(rc).write(config / "rc.xml", encoding="utf-8")
            env.update(
                WAYLAND_DISPLAY="wayland-0",
                WLR_BACKENDS="headless",
                WLR_HEADLESS_OUTPUTS="1",
                WLR_RENDERER="pixman",
                WLR_LIBINPUT_NO_DEVICES="1",
            )
            start(
                "compositor",
                [args.labwc, "-C", config],
                {"LD_LIBRARY_PATH": args.labwc_lib} if args.labwc_lib else {},
            )
            wait("Wayland compositor", lambda: (run_dir / "wayland-0").exists())
        else:
            disp = str(args.display)
            env["DISPLAY"] = ":" + disp
            start(
                "xserver",
                [
                    "Xvfb",
                    ":" + disp,
                    "-screen",
                    "0",
                    "2200x1050x24",
                    "-nolisten",
                    "tcp",
                ],
            )
            wait("X11", lambda: cmd("xdpyinfo"))
            start("wm", ["openbox"])
            time.sleep(0.6)
        start("pipewire", ["pipewire"])
        wait("PipeWire", lambda: (run_dir / "pipewire-0").exists())
        start("manager", ["wireplumber", "-p", "policy"])
        start("pulse", ["pipewire-pulse"])
        wait("Pulse", lambda: cmd("pactl", "info"))
        cmd("pactl", "load-module", "module-null-sink", "sink_name=qa_output")
        wait(
            "test playback device",
            lambda: b"qa_output" in cmd("pactl", "list", "short", "sinks"),
        )
        cmd("pactl", "set-default-sink", "qa_output")
        if wl:
            start("portal-backend", ["/usr/lib/xdg-desktop-portal-wlr", "-l", "DEBUG"])
            start("portal", ["/usr/lib/xdg-desktop-portal"])
        start(
            "player",
            [
                "mpv",
                "--no-config",
                "--start=300",
                "--geometry=640x360+1480+60",
                "--no-osc",
                "--ao=pulse",
                "--audio-client-name=MeetingQAPlayer",
                "--vo=" + ("wlshm" if wl else "x11"),
                "--input-ipc-server=" + str(root / "mpv.sock"),
                "--title=" + title,
                video,
            ],
        )
        wait(
            "player audio stream",
            lambda: b"MeetingQAPlayer" in cmd("pactl", "list", "sink-inputs"),
        )
        palette = json.loads(cmd(app, "--read-system-appearance"))
        border = tuple(palette["Border"][c] for c in "RGB")
        highlight = tuple(palette["Highlight"][c] for c in "RGB")
        start("app", [app, "--" + args.protocol])
        wait("meeting DB", lambda: dbcount("select count(*) from meetings"))
        if wl:

            def focus_workbench():
                cmd(args.wtype, "-k", "F12")
                return focused.exists()

            wait("mapped workbench", focus_workbench)
        else:
            win = wait(
                "native window",
                lambda: next(
                    (
                        line.split()[0]
                        for line in cmd("xwininfo", "-root", "-tree")
                        .decode()
                        .splitlines()
                        if '"听序 ·' in line
                    ),
                    None,
                ),
            )
            cmd("xdotool", "windowmove", win, "8", "5")
            cmd("xdotool", "windowactivate", "--sync", win)
        time.sleep(2)
        try:
            press("ctrl+n")
            type_text("Player acceptance")
            press("ctrl+s")
            wait(
                "created meeting",
                lambda: dbcount(
                    "select count(*) from meetings where title='Player acceptance'"
                ),
            )
            press("ctrl+k")
            type_text("Review captured slide")
            press("ctrl+s")
            wait(
                "note",
                lambda: dbcount(
                    "select count(*) from notes where title='Review captured slide'"
                ),
            )
            press("ctrl+l")
            type_text("MeetingQAPlayer")
            press("ctrl+s")
            press("ctrl+s")
            wait(
                "selected player audio",
                lambda: dbcount(
                    "select count(*) from meetings where title='Player acceptance' and lower(source_label) like '%meetingqaplayer%'"
                ),
            )
            y, left, right = wait("idle audio meter", idle_meter)
            press("ctrl+r")

            def meter_lit():
                bar = toolbar_pixels()[y, left:right]
                return (bar == highlight).all(axis=1).sum() >= 3

            wait("toolbar meter reacting to player PCM", meter_lit)
            wait(
                "player audio at ASR",
                lambda: dbcount(
                    "select count(*) from events where text='Captured from player'"
                ),
            )
            print("Player audio transcribed via selected stream", flush=True)
            press("ctrl+g")
            if not wl:
                pwin = wait(
                    "X11 player",
                    lambda: next(
                        (
                            ln.split()[0]
                            for ln in cmd("xwininfo", "-root", "-tree")
                            .decode()
                            .splitlines()
                            if '"' + title + '"' in ln
                        ),
                        None,
                    ),
                )
                time.sleep(0.6)
                cmd(
                    "xdotool", "mousemove", "--window", pwin, "100", "100", "click", "1"
                )
            base = Path(env["XDG_DATA_HOME"]) / "tingsu/frames"
            frames = lambda: sorted(
                base.glob("*/screen-*.jpg"), key=lambda p: p.stat().st_mtime_ns
            )
            f1 = wait("captured player frame", lambda: frames() and frames()[-1])
            ref1 = root / "reference1.png"
            player(["screenshot-to-file", str(ref1), "video"])
            if not wl:
                cmd("xdotool", "windowactivate", "--sync", win)
            press("Escape")
            player(["seek", 330, "absolute+exact"])
            time.sleep(1.3)
            press("ctrl+j")
            f2 = wait(
                "second player frame", lambda: len(frames()) >= 2 and frames()[-1]
            )
            ref2 = root / "reference2.png"
            player(["screenshot-to-file", str(ref2), "video"])
            press("Escape")
            press("ctrl+r")
            framescores = [compare_frame(f1, ref1), compare_frame(f2, ref2)]
            assert min(framescores) > 0.65, framescores
            score = validate_audio(video, root)
            transcript = root / "archive.srt"
            transcript.write_text(
                "1\n00:02:03,400 --> 00:02:05,900\nA：待确认的议题\n", encoding="utf-8"
            )
            # This isolated compositor only implements ScreenCast. The actual
            # desktop file picker falls back to the normal in-app path form.
            press("ctrl+i")
            time.sleep(1.2)
            type_text(str(transcript))
            press("ctrl+s")
            wait(
                "imported timestamp and speaker",
                lambda: dbcount(
                    "select count(*) from events where speaker='A' and text='待确认的议题' and abs(at-123.4)<0.001"
                ),
            )
            result = {
                "timestamped_import": True,
                "live_audio_meter": True,
                "protocol": args.protocol,
                "external_player": "mpv",
                "audio_correlation": score,
                "frame_correlations": framescores,
            }
            (root / "result.json").write_text(json.dumps(result))
            print(json.dumps(result))
        finally:
            screen(root / "desktop.png")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--recording", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--protocol", choices=("wayland", "x11"), required=True)
    p.add_argument("--wtype", default="wtype")
    p.add_argument("--labwc", default="labwc")
    p.add_argument("--labwc-lib")
    p.add_argument("--display", type=int, default=1941)
    run(p.parse_args())
