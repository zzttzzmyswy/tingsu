"""Desktop audio discovery, timestamped PCM capture and optional desktop frames."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .runtime import child_options
from .screen_capture import ScreenCapture

__all__ = [
    "AudioSource",
    "AudioCapture",
    "ScreenCapture",
    "file_source",
    "list_audio_sources",
    "extract_video_frame",
]

RATE = 16_000
FFMPEG = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]


@dataclass(frozen=True)
class AudioSource:
    id: str
    label: str
    kind: str


def file_source(path: Path | str) -> AudioSource:
    path = Path(path).expanduser().resolve()
    return AudioSource(str(path), path.name, "file")


def _output(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=3, text=True, errors="replace"
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _json_records(args: list[str]) -> list[dict]:
    try:
        records = json.loads(_output(args))
    except ValueError:
        return []
    return records if isinstance(records, list) else []


def _pipewire_sources() -> list[AudioSource]:
    if not shutil.which("pw-record"):
        return []
    sources = []
    kinds = {
        "Audio/Source": ("mic", "麦克风"),
        "Audio/Source/Virtual": ("mic", "输入"),
        "Audio/Sink": ("monitor", "系统输出"),
        "Stream/Output/Audio": ("app", "应用"),
    }
    for entry in _json_records(["pw-dump"]):
        if not entry.get("type", "").endswith(":Node"):
            continue
        props = entry.get("info", {}).get("props", {})
        kind = kinds.get(props.get("media.class"))
        serial = props.get("object.serial")
        if not kind or serial is None:
            continue
        label = (
            props.get("application.name")
            or props.get("node.description")
            or props.get("node.nick")
            or props.get("node.name")
            or str(serial)
        )
        if kind[0] == "app" and props.get("media.name") not in (None, label):
            label += f" · {props['media.name']}"
        sources.append(
            AudioSource(str(serial), f"{kind[1]} · {label} (PipeWire)", f"pipewire-{kind[0]}")
        )
    return sources


def _pulse_records(group: str) -> list[dict]:
    result = _output(["pactl", "-f", "json", "list", group])
    if result:
        return json.loads(result)
    # pactl before PulseAudio 15 only provides text formats. Its short output
    # preserves the indices required by --monitor-stream, even without labels.
    records = []
    for line in _output(["pactl", "list", "short", group]).splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            if group == "sink-inputs":
                records.append({"index": fields[0], "sink": fields[1]})
            else:
                records.append({"index": fields[0], "name": fields[1]})
    return records


def _pulse_sources() -> list[AudioSource]:
    if not (shutil.which("pactl") and shutil.which("parec")):
        return []
    sources = []
    records = _pulse_records("sources")
    for item in records:
        name = item["name"]
        monitor = bool(item.get("monitor_source")) or name.endswith(".monitor")
        label = item.get("description") or name
        prefix = "系统输出" if monitor else "麦克风"
        kind = "pulse-monitor" if monitor else "pulse-mic"
        sources.append(AudioSource(name, f"{prefix} · {label} (PulseAudio)", kind))
    if records:
        for item in _pulse_records("sink-inputs"):
            props = item.get("properties", {})
            label = props.get("application.name") or props.get("media.name") or str(item["index"])
            if props.get("media.name") not in (None, label):
                label += f" · {props['media.name']}"
            sources.append(AudioSource(str(item["index"]), f"应用 · {label} (PulseAudio)", "pulse-app"))
    return sources


def _alsa_sources() -> list[AudioSource]:
    proc = Path("/proc/asound/pcm")
    if not proc.is_file() or not (shutil.which("arecord") or shutil.which("ffmpeg")):
        return []
    sources = []
    for line in proc.read_text(errors="replace").splitlines():
        match = re.match(r"(\d+)-(\d+):\s*([^:]+):.*\bcapture\s+[1-9]", line)
        if match:
            card, device, label = match.groups()
            address = f"plughw:{int(card)},{int(device)}"
            sources.append(
                AudioSource(address, f"麦克风 · {label.strip()} (ALSA {address})", "alsa-mic")
            )
    return sources


def list_audio_sources() -> list[AudioSource]:
    """Enumerate current devices and actively opened application playback streams."""
    if os.name == "nt":
        from .windows_capture import list_audio_sources as windows_sources

        return windows_sources()
    sources = _pipewire_sources() + _pulse_sources() + _alsa_sources()
    return sorted(sources, key=lambda s: {"mic": 0, "app": 1, "monitor": 2}[s.kind.split("-")[1]])


def _command(source: AudioSource, realtime: bool, offset: float) -> list[str]:
    if source.kind == "file":
        if not Path(source.id).is_file():
            raise ValueError(f"找不到录音或录屏文件：{source.id}")
        return [
            *FFMPEG,
            *(["-re"] if realtime else []),
            "-ss",
            str(offset),
            "-i",
            source.id,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-af",
            f"aresample={RATE}:async=1:first_pts=0",
            "-f",
            "s16le",
            "pipe:1",
        ]
    if source.kind.startswith("win-"):
        from .windows_capture import audio_command

        return audio_command(source)
    if source.kind.startswith("pipewire-"):
        props = {
            "node.dont-fallback": True,
            "node.dont-reconnect": True,
            "application.name": "Meeting Agent",
        }
        if source.kind == "pipewire-monitor":
            props["stream.capture.sink"] = True
        return [
            "pw-record",
            "--target",
            source.id,
            "-P",
            json.dumps(props),
            "--raw",
            "--format",
            "s16",
            "--rate",
            str(RATE),
            "--channels",
            "1",
            "-",
        ]
    if source.kind.startswith("pulse-"):
        args = [
            "parec",
            "--raw",
            "--format=s16le",
            f"--rate={RATE}",
            "--channels=1",
            "--latency-msec=100",
            "--client-name=Meeting Agent",
        ]
        if source.kind == "pulse-app":
            # PulseAudio needs the monitor of the sink on which this input is playing.
            inputs = _pulse_records("sink-inputs")
            sinks = _pulse_records("sinks")
            playback = next((i for i in inputs if str(i["index"]) == source.id), None)
            sink = next(
                (s for s in sinks if playback and str(s["index"]) == str(playback["sink"])), None
            )
            if not sink:
                raise RuntimeError("此应用的播放流已关闭。请让应用播放声音并刷新音源列表。")
            monitor = sink.get("monitor_source") or sink["name"] + ".monitor"
            return [*args, "--monitor-stream", source.id, "--device", monitor]
        return [*args, "--device", source.id]
    if source.kind == "alsa-mic":
        if shutil.which("arecord"):
            return [
                "arecord",
                "-q",
                "-D",
                source.id,
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-r",
                str(RATE),
                "-c",
                "1",
            ]
        return [
            *FFMPEG,
            "-f",
            "alsa",
            "-i",
            source.id,
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
    raise ValueError(f"未知音源类型：{source.kind}")


class AudioCapture:
    """One-shot background capture. Callbacks run on its worker thread.

    Timestamps use the media clock for files. start_seconds seeks a file and
    offsets its timestamps, or offsets timestamps only for a live device.
    stop() is nonblocking. on_done follows the last partial chunk and any error.
    """

    def __init__(
        self,
        source: AudioSource,
        output_dir: Path | str,
        on_chunk: Callable[[Path, float, float], None],
        on_error: Callable[[str], None],
        on_done: Callable[[], None],
        chunk_seconds: float = 12,
        realtime: bool = True,
        start_seconds: float = 0,
    ):
        if not math.isfinite(chunk_seconds) or not 0.05 <= chunk_seconds <= 600:
            raise ValueError("音频分片长度应在 0.05 到 600 秒之间")
        if not math.isfinite(start_seconds) or start_seconds < 0:
            raise ValueError("起始时间必须是非负数")
        self.source = source
        self.output_dir = Path(output_dir)
        self.on_chunk = on_chunk
        self.on_error = on_error
        self.on_done = on_done
        self.chunk_bytes = round(chunk_seconds * RATE) * 2
        self.realtime = realtime
        self.start_seconds = start_seconds
        self.position_seconds = start_seconds
        self._stop = threading.Event()
        self._process_lock = threading.Lock()
        self._process = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="meeting-audio")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Closing the producer also wakes a blocked pipe read on Windows.
        with self._process_lock:
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

    def join(self, timeout: float | None = None) -> None:
        if self._thread.ident is not None:
            self._thread.join(timeout)

    def _write(self, pcm: bytes, start: float) -> None:
        with tempfile.NamedTemporaryFile(
            dir=self.output_dir, prefix="audio-", suffix=".wav", delete=False
        ) as dest:
            path = Path(dest.name)
            try:
                with wave.open(dest, "wb") as wav:
                    wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                    wav.writeframes(pcm)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        self.on_chunk(path, start, start + len(pcm) / (RATE * 2))

    def _read(self, process: subprocess.Popen) -> None:
        pending = bytearray()
        cursor = self.start_seconds
        epoch = time.monotonic()
        while not self._stop.is_set():
            before = time.monotonic()
            block = process.stdout.read1(65536)
            if not block:
                break
            now = time.monotonic()
            if self.source.kind != "file" and now - before >= 0.15:
                # A paused application may produce no PCM at all. Preserve the
                # gap in the timeline when the next packet finally arrives.
                arrival = self.start_seconds + now - epoch
                expected = cursor + (len(pending) + len(block)) / (RATE * 2)
                if arrival - expected > 0.8:
                    boundary = len(pending) - len(pending) % 2
                    if boundary:
                        self._write(pending[:boundary], cursor)
                        del pending[:boundary]
                    cursor = arrival - (len(pending) + len(block)) / (RATE * 2)
            pending.extend(block)
            self.position_seconds = cursor + len(pending) / (RATE * 2)
            while len(pending) >= self.chunk_bytes:
                self._write(pending[: self.chunk_bytes], cursor)
                cursor += self.chunk_bytes / (RATE * 2)
                del pending[: self.chunk_bytes]
        complete = len(pending) - len(pending) % 2
        if complete:
            self._write(pending[:complete], cursor)

    def _run(self) -> None:
        try:
            if self._stop.is_set():
                return
            self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            args = _command(self.source, self.realtime, self.start_seconds)
            with tempfile.TemporaryFile() as errors, subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, **child_options()
            ) as process:
                try:
                    with self._process_lock:
                        self._process = process
                    self._read(process)
                finally:
                    if process.poll() is None and self._stop.is_set():
                        process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if not self._stop.is_set() and (process.returncode or self.source.kind != "file"):
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 1200))
                    detail = errors.read().decode(errors="replace").strip()
                    raise RuntimeError(detail or "音源已断开，请刷新音源并重新连接。")
        except Exception as error:
            self.on_error(f"采集音频失败：{error}")
        finally:
            with self._process_lock:
                self._process = None
            self.on_done()


def extract_video_frame(video: str | Path, seconds: float, output_path: str | Path) -> Path:
    """Decode the frame at the requested media time. Run outside the GUI thread."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("画面时间必须是非负数")
    video = Path(video).expanduser().resolve()
    if not video.is_file():
        raise ValueError(f"找不到录屏：{video}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    codec = "png" if output.suffix.lower() == ".png" else "mjpeg"
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.part")
    # The audio stream can continue after the last video timestamp. An accurate
    # seek then returns zero frames. Decode from the preceding keyframe and keep
    # the final displayed image in that case (also handles low-FPS recordings).
    try:
        os.close(os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600))
        for tail in (False, True):
            args = [*FFMPEG, "-y"]
            if tail:
                args.append("-noaccurate_seek")
            args += ["-ss", str(seconds), "-i", str(video), "-map", "0:v:0"]
            if not tail:
                args += ["-frames:v", "1"]
            # Frames from an earlier keyframe have negative output timestamps
            # after this seek. Rebase them so the image muxer keeps the last slide.
            resize = "scale='min(iw,1920)':-2"
            args += [
                "-vf",
                ("setpts=PTS-STARTPTS," + resize) if tail else resize,
                "-c:v",
                codec,
                "-update",
                "1",
                "-f",
                "image2",
                str(temporary),
            ]
            result = subprocess.run(args, capture_output=True, timeout=25, **child_options())
            if result.returncode and not tail:
                temporary.write_bytes(b"")
                continue
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace").strip()[-500:] or "不能读取视频轨道")
            if temporary.stat().st_size:
                os.replace(temporary, output)
                return output
        raise RuntimeError("这个时间点没有可读取的画面")
    finally:
        temporary.unlink(missing_ok=True)
