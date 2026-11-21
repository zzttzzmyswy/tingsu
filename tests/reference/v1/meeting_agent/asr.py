"""Chunked meeting transcription with optional cross-request speaker matching."""

from __future__ import annotations

import io
import json
import math
import os
import re
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

import requests

SILICONFLOW_MODELS = (
    "XingChenAGI/XingChenASR-Diarize-V3.0",
    "XingChenAGI/XingChenASR-V3.2-Ultra",
    "XingChenAGI/XingChenGSR-V1.0",
)
DEFAULT_SOPHNET_MODEL = "Fun-ASR"
SOPHNET_URL = "https://www.sophnet.com/api/open-apis/projects/easyllms/speechtotext/transcriptions"
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"


class ASRError(RuntimeError):
    """Displayable error. Messages exclude request headers and raw response bodies."""


@dataclass(frozen=True)
class ASRConfig:
    provider: str = "siliconflow"
    api_key: str = field(default="", repr=False)
    model: str = ""


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: str
    text: str


@dataclass(frozen=True)
class _Audio:
    rate: int
    channels: int
    width: int
    pcm: bytes

    @property
    def bytes_per_frame(self):
        return self.channels * self.width

    @property
    def duration(self):
        return len(self.pcm) / (self.rate * self.bytes_per_frame)

    def clip(self, start, stop):
        a, b = (round(t * self.rate) * self.bytes_per_frame for t in (start, stop))
        return self.pcm[a:b]

    def wav(self):
        out = io.BytesIO()
        with wave.open(out, "wb") as f:
            f.setparams((self.channels, self.width, self.rate, 0, "NONE", "not compressed"))
            f.writeframes(self.pcm)
        return out.getvalue()


class SpeakerTracker:
    """Keep recent voice references in RAM. Use one instance per meeting, in time order.

    Reference audio is sent only to the same provider/model as the source chunk.
    Speaker numbers are estimates; a short or changed voice may get a new number.
    first_id can continue numbering after a process restart.
    """

    def __init__(self, first_id: int = 1):
        self._next = max(1, first_id)
        self._clips: dict[str, bytes] = {}
        self._source = None

    def _prepare(self, audio, source):
        signature = (source, audio.rate, audio.channels, audio.width)
        if signature != self._source:
            self._clips.clear()
            self._source = signature
        prefix = bytearray()
        references = []
        # Silence between references lets the recognizer separate them from new speech.
        silence = bytes(round(audio.rate * 1.0) * audio.bytes_per_frame)
        byte_rate = audio.rate * audio.bytes_per_frame
        for speaker, clip in self._clips.items():
            begin = len(prefix) / byte_rate
            prefix.extend(clip)
            references.append((begin, len(prefix) / byte_rate, speaker))
            prefix.extend(silence)
        return replace(audio, pcm=bytes(prefix) + audio.pcm), references, len(prefix) / byte_rate

    def _assign(self, audio, turns, references, prefix):
        # Match request-local labels to prior speakers only when a reference has
        # meaningful speech overlap and the labels agree unambiguously.
        links: dict[str, set[str]] = {}
        reverse: dict[str, set[str]] = {}
        for turn in turns:
            if not turn.speaker or turn.start >= prefix:
                continue
            for start, end, speaker in references:
                overlap = min(end, turn.end) - max(start, turn.start)
                if overlap >= min(0.65, (end - start) / 2):
                    links.setdefault(turn.speaker, set()).add(speaker)
                    reverse.setdefault(speaker, set()).add(turn.speaker)
        mapping = {}
        for local, possibilities in links.items():
            if len(possibilities) == 1:
                speaker = next(iter(possibilities))
                if len(reverse[speaker]) == 1:
                    mapping[local] = speaker

        result = []
        best = {}
        for turn in turns:
            if turn.end <= prefix:
                continue
            start = max(0.0, turn.start - prefix)
            end = min(audio.duration, turn.end - prefix)
            speaker = ""
            if turn.speaker:
                if turn.speaker not in mapping:
                    mapping[turn.speaker] = f"发言人 {self._next:02d}"
                    self._next += 1
                speaker = mapping[turn.speaker]
                if end - start > best.get(speaker, (0,))[0]:
                    best[speaker] = (end - start, start, end)
            result.append(Segment(start, end, speaker, turn.text))
        for speaker, (length, start, end) in best.items():
            if length < 1.25:
                continue
            # A representative stretch of one diarized utterance; don't mix turns.
            clip_start = start + max(0, (length - 7.0) / 2)
            clip = audio.clip(clip_start, min(end, clip_start + 7.0))
            existing = self._clips.pop(speaker, b"")
            self._clips[speaker] = clip if len(clip) >= len(existing) * 0.9 else existing
        while len(self._clips) > 4:
            del self._clips[next(iter(self._clips))]
        return result


def _load_audio(path):
    try:
        with wave.open(str(path), "rb") as f:
            if f.getnframes() * f.getnchannels() * f.getsampwidth() > 48_000_000:
                raise ASRError("音频分片超过 48 MB，请缩短采集分片。")
            return _Audio(
                f.getframerate(), f.getnchannels(), f.getsampwidth(), f.readframes(f.getnframes())
            )
    except (OSError, wave.Error, EOFError) as e:
        raise ASRError("无法读取 PCM WAV 音频分片。") from e


def _wait(seconds, cancel):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise ASRError("转写已取消。")


def _request(session, method, url, cancel, **kwargs):
    for attempt in range(2):
        if cancel is not None and cancel.is_set():
            raise ASRError("转写已取消。")
        try:
            r = session.request(method, url, timeout=(6, 40), allow_redirects=False, **kwargs)
        except requests.Timeout:
            raise ASRError("语音识别服务超时，请稍后重试。") from None
        except requests.RequestException:
            raise ASRError("无法连接语音识别服务，请检查网络。") from None
        if r.status_code in (429, 503) and not attempt:
            try:
                delay = min(3.0, max(0.5, float(r.headers.get("Retry-After", 1))))
            except ValueError:
                delay = 1.0
            _wait(delay, cancel)
            continue
        if not 200 <= r.status_code < 300:
            message = {
                400: "请求参数或模型有误",
                401: "密钥无效",
                402: "账户余额不足",
                403: "无权访问此模型",
                404: "项目或模型不存在",
                422: "服务无法处理音频或模型参数",
                429: "请求频率或配额已达上限",
            }.get(r.status_code, "服务请求失败")
            raise ASRError(f"语音识别{message}（HTTP {r.status_code}）。")
        try:
            return r.json()
        except ValueError:
            raise ASRError("语音识别服务返回了无法解析的响应。") from None


def _get_result(session, audio, provider, model, cancel):
    file = ("chunk.wav", audio.wav(), "audio/wav")
    if provider == "siliconflow":
        fields = {"file": file, "model": (None, model), "response_format": (None, "verbose_json")}
        return _request(session, "POST", SILICONFLOW_URL, cancel, files=fields)

    # Older workspaces stored an EasyLLM deployment ID in the model field.
    legacy_id = re.fullmatch(r"[A-Za-z0-9]{22}", model)
    params = {"speech_recognition_param": {
        "model": DEFAULT_SOPHNET_MODEL if legacy_id else model, "diarization_enabled": True
    }}
    if legacy_id:
        params["easyllm_id"] = model
    fields = {"audio_file": file, "data": (None, json.dumps(params), "application/json")}
    reply = _request(session, "POST", SOPHNET_URL, cancel, files=fields)
    if not isinstance(reply, dict):
        raise ASRError("Sophnet 返回了无法解析的任务响应。")
    task = reply.get("taskId") or reply.get("task_id")
    if not task:
        return reply
    url = SOPHNET_URL + "/" + quote(str(task), safe="")
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        _wait(1, cancel)
        reply = _request(session, "GET", url, cancel)
        if not isinstance(reply, dict):
            break
        state = str(reply.get("status", "")).upper()
        if state in {"SUCCEEDED", "SUCCESS", "COMPLETED"}:
            return reply
        if state in {"FAILED", "CANCELED", "CANCELLED", "ERROR"}:
            raise ASRError("Sophnet 未能完成此次转写。")
        if state not in {"PENDING", "RUNNING", "WAITING", "DOING", "QUEUED", "PROCESSING"}:
            raise ASRError("Sophnet 返回了无法识别的任务状态。")
    raise ASRError("等待 Sophnet 转写任务超时。")


def _turn(row, duration, milliseconds=False):
    if not isinstance(row, dict) or not isinstance(row.get("text"), str):
        return None
    text = row["text"].strip()
    if not text:
        return None
    try:
        start = float(row["begin_time"] if milliseconds else row["start"])
        end = float(row["end_time"] if milliseconds else row["end"])
        if milliseconds:
            start, end = start / 1000, end / 1000
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    start, end = max(0.0, start), min(duration, end)
    if end <= start:
        return None
    speaker = row.get("speaker", row.get("speaker_id"))
    return Segment(start, end, "" if speaker is None else str(speaker).strip(), text)


def _decode(data, duration):
    """Return timed turns, or a chunk-level time range when the service sent only text."""
    if not isinstance(data, dict):
        raise ASRError("语音识别服务返回了未知的数据格式。")
    if isinstance(data.get("segments"), list) and data["segments"]:
        turns = [_turn(row, duration) for row in data["segments"]]
        if all(t is not None for t in turns):
            return turns, True
    text = data.get("text", data.get("result"))
    if isinstance(data.get("results"), list):
        chunks, sentences = [], []
        all_timed = True
        for result in data["results"]:
            if not isinstance(result, dict):
                raise ASRError("Sophnet 返回了无法解析的结果。")
            if str(result.get("subtask_status", "")).upper() == "FAILED":
                raise ASRError("Sophnet 未能识别音频分片。")
            for transcript in result.get("transcripts", []):
                if not isinstance(transcript, dict):
                    raise ASRError("Sophnet 返回了无法解析的转写。")
                chunks.append(str(transcript.get("text") or ""))
                if chunks[-1] and not transcript.get("sentences"):
                    all_timed = False
                for row in transcript.get("sentences", []):
                    turn = _turn(row, duration, milliseconds=True)
                    if turn:
                        sentences.append(turn)
                    else:
                        all_timed = False
        if sentences and all_timed:
            return sentences, True
        text = "\n".join(chunks)
    if isinstance(text, str):
        text = text.strip()
        return ([Segment(0.0, duration, "", text)] if text else []), False
    raise ASRError("语音识别服务未返回可读取的转写结果。")


def transcribe(
    wav_path: str | Path,
    config: ASRConfig,
    *,
    offset: float = 0.0,
    tracker: SpeakerTracker | None = None,
    cancel: threading.Event | None = None,
) -> list[Segment]:
    """Upload one PCM WAV and return seconds relative to the meeting time origin.

    offset is the original chunk's start, even if speaker reference audio is used.
    Calls sharing a tracker must be serialized by the caller.
    """
    provider = config.provider.lower().strip()
    if provider not in {"sophnet", "siliconflow"}:
        raise ASRError("未知的语音识别供应商。")
    key = config.api_key.strip() or os.getenv(f"{provider.upper()}_API_KEY", "").strip()
    if not key:
        raise ASRError(f"请先设置 {provider} API 密钥。")
    model = config.model.strip() or (
        SILICONFLOW_MODELS[0] if provider == "siliconflow" else DEFAULT_SOPHNET_MODEL
    )
    audio = _load_audio(wav_path)
    if not audio.pcm:
        return []
    if audio.width == 2:
        # Skip digital silence and very low-level quantization noise, without a VAD cutoff.
        samples = array("h", audio.pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if max(samples) <= 8 and min(samples) >= -8:
            return []

    use_tracker = tracker is not None and (provider == "sophnet" or model == SILICONFLOW_MODELS[0])
    upload, references, prefix = (
        tracker._prepare(audio, (provider, model)) if use_tracker else (audio, [], 0.0)
    )
    with requests.Session() as session:
        session.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
        data = _get_result(session, upload, provider, model, cancel)
        turns, timed = _decode(data, upload.duration)
        if (
            prefix
            and turns
            and (not timed or any(t.start < prefix - 0.25 and t.end > prefix for t in turns))
        ):
            # A service may merge text across the reference boundary. Repeat without
            # references so older speech cannot leak into the meeting timeline.
            turns, timed = _decode(_get_result(session, audio, provider, model, cancel), audio.duration)
            references, prefix = [], 0.0
    if use_tracker:
        turns = tracker._assign(audio, turns, references, prefix)
    else:
        turns = [replace(t, speaker=f"发言人 {t.speaker}" if t.speaker else "") for t in turns]
    return [replace(turn, start=offset + turn.start, end=offset + turn.end) for turn in turns]
