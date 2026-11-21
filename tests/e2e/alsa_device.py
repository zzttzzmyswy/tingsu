"""Run the released GUI against a private ALSA PCM and the live Sophnet ASR.

Export SOPHNET_API_KEY, then run from the project root:
    python3 tests/e2e/alsa_device.py --app dist/Tingsu-Linux-x86_64.AppImage

Requires Linux with /proc/asound/pcm, bubblewrap, Xvfb, xauth, xvfb-run, espeak,
FFmpeg and the Python package websocket-client. The test starts its own display
and hides the sound devices. Only the synthetic English voice goes to Sophnet.
"""

import argparse
import contextlib
import json
import multiprocessing
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from desktop import Page, wait

TITLE = "ALSA synthetic meeting"
SOURCE = "alsa-mic:plughw:98,0"
RATE = 16000
CHUNK_SECONDS = 8


def make_speech(folder):
    wav = folder / "speech.wav"
    subprocess.run(
        [
            "espeak",
            "-v",
            "en",
            "-s",
            "145",
            "-w",
            str(wav),
            "The product review is on Friday morning. Jordan will bring the slides. Our meeting starts at ten.",
        ],
        check=True,
    )
    pcm = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(wav),
            "-ar",
            str(RATE),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-",
        ]
    )
    assert 0 < len(pcm) <= RATE * 2 * CHUNK_SECONDS
    return pcm.ljust(RATE * 2 * CHUNK_SECONDS, b"\0")


def feed(fifo, pcm):
    # arecord's default period on this null slave is 125 ms. Short FIFO reads
    # are zero-padded by the ALSA file plug-in, so deliver a whole period atomically.
    period = RATE * 2 // 8
    try:
        with fifo.open("wb", buffering=0) as writer:
            sent = 0
            started = time.monotonic()
            while True:
                block = pcm[sent : sent + period] if sent < len(pcm) else bytes(period)
                writer.write(block)
                sent += len(block)
                time.sleep(max(0, started + sent / (2 * RATE) - time.monotonic()))
    except BrokenPipeError:
        pass


def stop_group(process):
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def run(app, folder):
    pcm = make_speech(folder)
    fifo = folder / "speech.fifo"
    os.mkfifo(fifo, 0o600)
    devices = folder / "pcm"
    devices.write_text("98-00: Tingsu virtual speech : virtual input : capture 1\n")
    config = folder / "asound.conf"
    config.write_text(
        "pcm.fixture_null { type null }\n"
        "pcm.!plughw {\n"
        "  @args [ CARD DEV ]\n"
        "  @args.CARD { type integer }\n"
        "  @args.DEV { type integer }\n"
        '  type file\n  slave.pcm "fixture_null"\n  file "/dev/null"\n  format "raw"\n'
        f"  infile {json.dumps(str(fifo))}\n}}\n"
    )
    env = os.environ.copy()
    for name in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
    ):
        directory = folder / name.lower()
        directory.mkdir(mode=0o700)
        env[name] = str(directory)
    for name in (
        "SILICONFLOW_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "WAYLAND_DISPLAY",
        "WAYLAND_SOCKET",
        "KDE_FULL_SESSION",
        "SESSION_MANAGER",
        "PIPEWIRE_RUNTIME_DIR",
        "PULSE_RUNTIME_PATH",
    ):
        env.pop(name, None)
    env.update(
        ALSA_CONFIG_PATH=str(config),
        XDG_SESSION_TYPE="x11",
        XDG_CURRENT_DESKTOP="tingsu-fixture",
        QT_QPA_PLATFORM="xcb",
        LIBGL_ALWAYS_SOFTWARE="1",
        DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(folder / "no-user-bus"),
        PIPEWIRE_REMOTE="no-private-server",
        PULSE_SERVER="unix:" + str(folder / "no-pulse"),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
    cmd = [
        "xvfb-run",
        "-a",
        "-s",
        "-screen 0 1280x800x24",
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--tmpfs",
        "/dev/shm",
        "--bind",
        str(folder),
        str(folder),
        "--proc",
        "/proc",
        "--ro-bind",
        str(devices),
        "/proc/asound/pcm",
        "/bin/sh",
        "-c",
        'test ! -e /dev/snd && exec "$@"',
        "test-app",
        str(app),
        "--appimage-extract-and-run",
        "--x11",
    ]
    data = Path(env["XDG_DATA_HOME"]) / "tingsu"
    with contextlib.ExitStack() as cleanup:
        producer = multiprocessing.Process(target=feed, args=(fifo, pcm))
        producer.start()
        cleanup.callback(producer.join)
        cleanup.callback(producer.terminate)
        log = cleanup.enter_context((folder / "app.log").open("w"))
        process = subprocess.Popen(
            cmd, env=env, cwd=folder, stdout=log, stderr=log, start_new_session=True
        )
        cleanup.callback(stop_group, process)
        page = Page(port)
        cleanup.callback(page.close)
        page.click("settings")
        page.field("#provider", "sophnet")
        page.field("#chunk", str(CHUNK_SECONDS))
        page.click("settings-save")
        page.click("new")
        page.until(
            f"Array.from(document.querySelector('#source-select').options).some(o=>o.value==={json.dumps(SOURCE)} && o.textContent.includes('Tingsu virtual speech'))"
        )
        page.field("#source-select", SOURCE)
        page.field("#meeting-name", TITLE)
        page.click("create-start")
        page.until(f"S.meeting.title==={json.dumps(TITLE)} && S.capture==='recording'")

        def captured_expected_pcm():
            for path in (data / "audio").rglob("*.wav"):
                try:
                    with wave.open(str(path), "rb") as audio:
                        if audio.getnframes() != RATE * CHUNK_SECONDS:
                            continue
                        captured = audio.readframes(audio.getnframes())
                except (EOFError, wave.Error, FileNotFoundError):
                    continue
                if len(captured) == len(pcm):
                    assert (
                        captured == pcm
                    ), "The virtual ALSA input differs from the generated speech"
                    return True
            return False

        wait(
            captured_expected_pcm, "eight-second WAV from the selected ALSA device", 35
        )
        time.sleep(1.4)
        page.click("stop")
        page.until("S.capture==='idle'", 120)
        view = page.js("document.querySelector('#feed-list').innerText")
        with contextlib.closing(sqlite3.connect(data / "studio.sqlite3")) as db:
            rows = list(
                db.execute(
                    "SELECT e.at,e.end,e.speaker,e.text FROM events e JOIN meetings m ON e.meeting=m.id "
                    "WHERE m.title=? AND m.source=? AND e.kind='speech' ORDER BY e.seq",
                    (TITLE, SOURCE),
                )
            )
            assert (
                db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
            ), "Pending audio after stop"
        spoken = " ".join(text for _, _, _, text in rows).lower()
        assert all(word in spoken for word in ("friday", "slides")), rows
        assert all(
            0 <= start < end <= CHUNK_SECONDS
            and speaker
            and speaker in view
            and text in view
            for start, end, speaker, text in rows
        ), rows
        print("Verified virtual ALSA PCM, GUI transcript and empty queue:", rows)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--app", required=True, type=Path)
    args = parser.parse_args()
    if not os.getenv("SOPHNET_API_KEY"):
        parser.error("Export SOPHNET_API_KEY before running this live test")
    if not Path("/proc/asound/pcm").is_file():
        parser.error(
            "This isolated test needs an existing /proc/asound/pcm as its bind mount target"
        )
    app = args.app.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="tingsu-alsa-e2e-") as temp:
        folder = Path(temp)
        try:
            run(app, folder)
        except Exception:
            if (log := folder / "app.log").exists():
                print(log.read_text(errors="replace")[-2000:])
            raise


if __name__ == "__main__":
    main()
