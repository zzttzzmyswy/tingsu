import subprocess
import sys
import threading
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import json

from meeting_agent.capture import AudioCapture, AudioSource, extract_video_frame


class CaptureTests(unittest.TestCase):
    def test_seek_in_audio_tail_returns_last_video_frame(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            movie = folder / "two slides.mp4"
            size = (96, 64)
            red = bytes((240, 20, 20)) * (96 * 64)
            blue = bytes((20, 20, 240)) * (96 * 64)
            # Frames at 0 and 1 s, followed by a further two seconds of audio.
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "rgb24",
                    "-video_size",
                    "96x64",
                    "-framerate",
                    "1",
                    "-i",
                    "pipe:0",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=4",
                    "-c:v",
                    "mpeg4",
                    "-g",
                    "12",
                    "-sc_threshold",
                    "1000000000",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(movie),
                ],
                input=red + blue,
                capture_output=True,
                check=True,
                timeout=20,
            )
            for seconds, expected in ((0, (240, 20, 20)), (3.95, (20, 20, 240))):
                with self.subTest(seconds=seconds):
                    path = extract_video_frame(movie, seconds, folder / "frame.jpg")
                    stream = json.loads(subprocess.check_output(
                        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                         "-of", "json", str(path)]))["streams"][0]
                    self.assertEqual((stream["width"], stream["height"]), size)
                    actual = subprocess.check_output(
                        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=1:1",
                         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
                    for obtained, wanted in zip(actual, expected):
                        self.assertAlmostEqual(obtained, wanted, delta=8)

    def test_stop_releases_a_blocked_pcm_pipe_and_flushes_the_tail(self):
        with TemporaryDirectory() as directory:
            ready = threading.Event()
            finished = threading.Event()
            errors = []
            chunks = []
            order = []

            def collect(path, start, end):
                with wave.open(str(path), "rb") as audio:
                    self.assertEqual(audio.getframerate(), 16000)
                    chunks.append((start, end, audio.readframes(audio.getnframes())))
                order.append("chunk")
                ready.set()

            def done():
                order.append("done")
                finished.set()

            # Write one complete chunk plus 10 ms and then hold stdout open.
            # Ignoring SIGTERM also covers a POSIX producer that stalls during shutdown.
            producer = [
                sys.executable,
                "-c",
                "import signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "sys.stdout.buffer.write(b'\\x21\\x13'*960);sys.stdout.buffer.flush();time.sleep(8)",
            ]
            capture = AudioCapture(
                AudioSource("fixture", "Fixture", "win-mic"),
                directory,
                collect,
                errors.append,
                done,
                chunk_seconds=0.05,
            )
            with patch("meeting_agent.capture._command", return_value=producer):
                capture.start()
                try:
                    self.assertTrue(ready.wait(3), errors)
                    # The producer has written everything and the worker now waits for more PCM.
                    time.sleep(0.1)
                    started = time.monotonic()
                    capture.stop()
                    self.assertLess(time.monotonic() - started, 1.0)
                    self.assertTrue(
                        finished.wait(2),
                        "The read from the idle producer stayed blocked",
                    )
                finally:
                    capture.stop()
                    capture.join(10)
            self.assertEqual(errors, [])
            self.assertEqual(order, ["chunk", "chunk", "done"])
            self.assertEqual([len(pcm) for _, _, pcm in chunks], [1600, 320])
            self.assertEqual(b"".join(pcm for _, _, pcm in chunks), b"\x21\x13" * 960)
            self.assertEqual(chunks[0][:2], (0, 0.05))
            self.assertAlmostEqual(chunks[1][0], 0.05)
            self.assertAlmostEqual(chunks[1][1], 0.06)
