"""Capture and drain one meeting's durable transcription queue without a GUI event loop."""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .asr import ASRConfig, SpeakerTracker, transcribe
from .capture import AudioCapture


@dataclass
class _Run:
    meeting: str
    config: ASRConfig
    tracker: SpeakerTracker
    jobs: queue.Queue = field(default_factory=queue.Queue)
    capture: AudioCapture | None = None
    worker: threading.Thread | None = None
    draining: bool = False
    input_failed: bool = False


class TranscriptionService:
    """Callbacks may run on the caller, capture, or recognition thread.

    stop() drains captured audio. close() cancels recognition and leaves the tail
    and unfinished WAVs in the store so a new process can retry them.
    """

    def __init__(self, store, audio_root, *, changed, transcribed, failed, completed,
                 capture_factory=AudioCapture, recognize=transcribe):
        self.store = store
        self.audio_root = Path(audio_root)
        self.changed = changed
        self.transcribed = transcribed
        self.failed = failed
        self.completed = completed
        self._capture_factory = capture_factory
        self._recognize = recognize
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._active = None
        self._speaker_key = None
        self._tracker = None

    @property
    def status(self):
        with self._lock:
            if self._active is None:
                return "idle"
            return "draining" if self._active.draining else "recording"

    @property
    def position(self):
        with self._lock:
            if self._active and self._active.capture:
                return self._active.capture.position_seconds
            return None

    def ensure_idle(self):
        with self._lock:
            if self._closed.is_set():
                raise ValueError("转写服务已关闭。")
            if self._active is not None:
                raise ValueError("请先停止采集，并等待已录音频完成转写。")

    def forget_speakers(self):
        with self._lock:
            self.ensure_idle()
            self._speaker_key = self._tracker = None

    def _speaker_tracker(self, mid, config):
        key = (mid, config.provider, config.model)
        if self._speaker_key != key:
            numbers = [int(match[1]) for speaker in self.store.speech_speakers(mid)
                       if (match := re.fullmatch(r"发言人 (\d+)", speaker))]
            self._tracker = SpeakerTracker(first_id=max(numbers, default=0) + 1)
            self._speaker_key = key
        return self._tracker

    def _new_run(self, mid, config):
        if not config.api_key:
            raise ValueError("请先在设置中填写转写供应商的 API 密钥。")
        return _Run(mid, config, self._speaker_tracker(mid, config))

    def record(self, mid, source, config, *, chunk_seconds, start_seconds):
        with self._lock:
            self.ensure_idle()
            if self.store.pending_count(mid):
                raise ValueError("请先重试尚未转写的音频。")
            run = self._new_run(mid, config)
            run.capture = self._capture_factory(
                source, self.audio_root / mid / uuid4().hex,
                lambda path, start, end: self._enqueue(run, path, start, end),
                lambda message: self._input_error(run, message),
                lambda: self._end_input(run),
                chunk_seconds=chunk_seconds, realtime=True, start_seconds=start_seconds,
            )
            self._start_worker(run)
            try:
                run.capture.start()
            except Exception:
                run.input_failed = True
                self._end_input(run)
                raise
        self.changed()

    def retry(self, mid, config):
        with self._lock:
            self.ensure_idle()
            run = self._new_run(mid, config)
            run.draining = True
            for job in self.store.pending_jobs(mid):
                run.jobs.put((Path(job["path"]), job["start"], job["end"]))
            run.jobs.put(None)
            self._start_worker(run)
        self.changed()

    def _start_worker(self, run):
        run.worker = threading.Thread(target=self._consume, args=(run,), name="meeting-asr", daemon=True)
        self._active = run
        try:
            run.worker.start()
        except Exception:
            self._active = None
            raise

    def _enqueue(self, run, path, start, end):
        self.store.queue(run.meeting, path, start, end)
        run.jobs.put((Path(path), start, end))
        self.changed()

    def _input_error(self, run, message):
        run.input_failed = True
        self.failed(run.meeting, message)

    def _end_input(self, run):
        with self._lock:
            run.draining = True
        run.jobs.put(None)
        self.changed()

    def _consume(self, run):
        failed = False
        while (job := run.jobs.get()) is not None:
            if failed or self._closed.is_set():
                continue
            path, start, end = job
            try:
                segments = self._recognize(
                    path, run.config, offset=start, tracker=run.tracker, cancel=self._closed
                )
                if self._closed.is_set():
                    continue
                self.store.finish_job(run.meeting, path, segments)
            except Exception as exc:
                failed = True
                if run.capture:
                    run.capture.stop()
                if not self._closed.is_set():
                    self.failed(run.meeting, exc)
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self.failed(run.meeting, f"转写已保存，清理临时音频失败：{exc}")
            self.transcribed(run.meeting, end, bool(segments))
        with self._lock:
            self._active = None
        self.changed()
        if not (failed or run.input_failed or self._closed.is_set()):
            self.completed(run.meeting)

    def stop(self):
        with self._lock:
            run = self._active
            if not run or not run.capture or run.draining:
                return
            run.draining = True
        run.capture.stop()
        self.changed()

    def close(self, timeout=5):
        with self._lock:
            self._closed.set()
            run = self._active
            if not run:
                return
            run.draining = True
        deadline = time.monotonic() + timeout
        if run.capture:
            run.capture.stop()
            run.capture.join(timeout)
        run.worker.join(max(0, deadline - time.monotonic()))
