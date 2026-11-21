"""Windows WASAPI discovery and selected-window stills using the bundled native helper."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QInputDialog


def _helper() -> str:
    internal = Path(__file__).resolve().parent / "native" / "tingsu-capture.exe"
    if internal.is_file():
        return str(internal)
    installed = shutil.which("tingsu-capture.exe")
    if installed:
        return installed
    raise RuntimeError("缺少 Windows 采集组件 tingsu-capture.exe，请使用完整的听序安装包。")


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _rows(command: str) -> list[dict]:
    output = subprocess.run(
        [_helper(), command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        creationflags=_flags(),
    )
    if output.returncode:
        message = output.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("无法访问 Windows 桌面设备。" + message[-400:])
    return [json.loads(line) for line in output.stdout.decode("utf-8").splitlines() if line]


def list_audio_sources():
    from .capture import AudioSource

    labels = {"win-mic": "麦克风", "win-monitor": "系统输出", "win-app": "应用"}
    return [
        AudioSource(row["id"], f'{labels[row["kind"]]} · {row["label"]} (WASAPI)', row["kind"])
        for row in _rows("audio-sources")
    ]


def audio_command(source) -> list[str]:
    if source.kind not in {"win-mic", "win-monitor", "win-app"}:
        raise ValueError("未知的 Windows 音频来源。")
    return [_helper(), "audio", source.kind, source.id]


def choose_window() -> str:
    """Select a Windows top-level window on the Qt GUI thread."""
    choices = {}
    for row in _rows("windows"):
        hwnd, pid, _ = row["id"].split("/")
        if pid != str(os.getpid()):
            choices[f'{row["label"]}  [{hwnd}]'] = row["id"]
    if not choices:
        raise ValueError("没有可共享的可见窗口。请先打开并还原会议窗口。")
    label, ok = QInputDialog.getItem(
        QApplication.activeWindow(), "选择会议窗口", "要共享的窗口", list(choices), 0, False
    )
    if not ok or label not in choices:
        raise ValueError("已取消窗口选择。")
    return choices[label]


def _grab(token: str, stop: Event) -> bytes | None:
    with subprocess.Popen(
        [_helper(), "frame", token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_flags(),
    ) as proc:
        deadline = time.monotonic() + 9
        try:
            while not stop.is_set():
                try:
                    bitmap, errors = proc.communicate(timeout=0.15)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("共享窗口未响应，请还原并重新选择会议窗口。")
                    continue
                if proc.returncode:
                    raise RuntimeError(
                        "Windows 窗口取帧失败。" + errors.decode(errors="replace")[-400:]
                    )
                image = QImage.fromData(bitmap, "BMP")
                if image.isNull():
                    raise RuntimeError("所选窗口没有可读取的画面。")
                if image.width() > 1920:
                    image = image.scaledToWidth(1920, Qt.TransformationMode.SmoothTransformation)
                encoded = QBuffer()
                encoded.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(encoded, "JPEG", 88):
                    raise RuntimeError("无法编码当前窗口截图。")
                return bytes(encoded.data())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    return None


def capture_windows_frames(
    token: str, stop: Event, wanted: Event, on_jpeg: Callable[[bytes], None]
) -> None:
    """Run in ScreenCapture's worker. Each request revalidates the HWND and process birth time."""
    while not stop.is_set():
        if wanted.wait(0.15):
            wanted.clear()
            jpeg = _grab(token, stop)
            if jpeg is not None and not stop.is_set():
                on_jpeg(jpeg)
