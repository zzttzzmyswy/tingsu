import threading
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from meeting_agent.asr import ASRConfig, Segment
from meeting_agent.storage import Store
from meeting_agent.transcription_service import TranscriptionService


class TailCapture:
    """An audio producer that flushes its last partial WAV only when stopped."""

    def __init__(self, source, directory, on_chunk, on_error, on_done, *, start_seconds, **options):
        self.position_seconds = start_seconds
        self.directory = directory
        self.on_chunk = on_chunk
        self.on_done = on_done
        self.stopped = False

    def start(self):
        self.directory.mkdir(parents=True)
        self.emit_chunk(1)

    def emit_chunk(self, seconds):
        start = self.position_seconds
        path = self.directory / f"{start}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x01" * round(16000 * seconds))
        self.position_seconds += seconds
        self.on_chunk(path, start, self.position_seconds)

    def stop(self):
        if not self.stopped:
            self.stopped = True
            self.emit_chunk(0.25)
            self.on_done()

    def join(self, timeout):
        pass


class TranscriptionServiceTests(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.root = Path(resources.enter_context(TemporaryDirectory()))
        resources.enter_context(patch("meeting_agent.storage.DATA", self.root))
        self.store = Store()
        self.addCleanup(self.store.db.close)
        self.mid = self.store.create("会议")
        self.config = ASRConfig(provider="sophnet", api_key="test")
        self.idle = threading.Event()
        self.complete = threading.Event()
        self.attempted = threading.Event()
        self.results = []
        self.errors = []

    def service(self, recognize, store=None):
        def changed():
            if service.status == "idle":
                self.idle.set()

        service = TranscriptionService(
            store or self.store, self.root / "audio",
            changed=changed, transcribed=lambda *args: self.results.append(args),
            failed=lambda mid, error: self.errors.append(str(error)),
            completed=lambda mid: self.complete.set(),
            capture_factory=TailCapture, recognize=recognize,
        )
        self.addCleanup(service.close)
        return service

    def record(self, service):
        service.record(self.mid, "simulated mic", self.config, chunk_seconds=4, start_seconds=30)
        self.assertTrue(self.attempted.wait(3))

    @staticmethod
    def recognize(path, config, *, offset, tracker, cancel):
        with wave.open(str(path)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        return [Segment(offset, offset + duration, "发言人 01", path.stem)]

    def test_graceful_stop_drains_the_tail_before_becoming_idle(self):
        release = threading.Event()

        def delayed(*args, **kwargs):
            self.attempted.set()
            release.wait(3)
            return self.recognize(*args, **kwargs)

        service = self.service(delayed)
        self.record(service)
        self.assertEqual(service.position, 31)
        self.assertEqual(service.status, "recording")
        with self.assertRaisesRegex(ValueError, "先停止"):
            service.retry(self.store.create("另一场会议"), self.config)
        service.stop()
        self.assertEqual(service.status, "draining")
        self.assertEqual(self.store.pending_count(self.mid), 2)
        release.set()
        self.assertTrue(self.complete.wait(3))
        self.assertEqual(self.results, [(self.mid, 31, True), (self.mid, 31.25, True)])
        self.assertEqual(service.status, "idle")
        self.assertEqual(self.store.pending_count(self.mid), 0)
        self.assertEqual(list(self.root.rglob("*.wav")), [])
        self.assertEqual(self.errors, [])

    def test_close_preserves_in_flight_and_tail_for_another_process(self):
        def blocked(*args, cancel, **kwargs):
            self.attempted.set()
            self.assertTrue(cancel.wait(3))
            return self.recognize(*args, cancel=cancel, **kwargs)

        original = self.service(blocked)
        self.record(original)
        original.close()
        self.assertEqual(self.store.events(self.mid), [])
        self.assertEqual(self.store.meeting(self.mid)["duration"], 31.25)
        self.assertFalse(self.complete.is_set())
        reopened = Store()
        self.addCleanup(reopened.db.close)
        resumed = self.service(self.recognize, reopened)
        with self.assertRaisesRegex(ValueError, "先重试"):
            resumed.record(self.mid, "simulated mic", self.config, chunk_seconds=4, start_seconds=31.25)
        self.assertTrue(all(Path(j["path"]).is_file() for j in reopened.pending_jobs(self.mid)))
        resumed.retry(self.mid, self.config)
        self.assertTrue(self.complete.wait(3))
        self.assertEqual([event["at"] for event in reopened.events(self.mid)], [30, 31])
        self.assertEqual(reopened.pending_jobs(self.mid), [])

    def test_failed_commit_keeps_wavs_for_retry(self):
        def attempted(*args, **kwargs):
            self.attempted.set()
            return self.recognize(*args, **kwargs)

        service = self.service(attempted)
        with patch.object(self.store, "finish_job", side_effect=RuntimeError("write failed")):
            self.record(service)
            self.assertTrue(self.idle.wait(3))
        self.assertEqual(self.errors, ["write failed"])
        self.assertFalse(self.complete.is_set())
        self.assertEqual(self.store.pending_count(self.mid), 2)
        self.assertTrue(all(Path(j["path"]).is_file() for j in self.store.pending_jobs(self.mid)))
        service.retry(self.mid, self.config)
        self.assertTrue(self.complete.wait(3))
        self.assertEqual([event["end"] for event in self.store.events(self.mid)], [31, 31.25])
