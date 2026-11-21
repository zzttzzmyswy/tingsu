import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

from .runtime import child_options
from .storage import CACHE, private_dir

MEDIA = {
    ".mkv",
    ".webm",
    ".mp4",
    ".mov",
    ".avi",
    ".m4v",
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".opus",
}


def thumbnail(path, width=640):
    image = QImage(str(path))
    if image.isNull():
        return ""
    image = image.scaled(
        width, width, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "JPEG", 80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.data().data()).decode()


def describe(path):
    path = Path(path)
    stat = path.stat()
    media_id = hashlib.sha256(f"2:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
    directory = private_dir(CACHE / "library")
    info = directory / (media_id + ".json")
    cover = directory / (media_id + ".jpg")
    if info.exists():
        data = json.loads(info.read_text(encoding="utf-8"))
    else:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            **child_options(),
            timeout=20,
            check=True,
        )
        probe = json.loads(probe.stdout)
        video = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), {})
        seconds = float(probe["format"].get("duration", 0))
        if video:
            for fraction in (0.12, 0.38, 0.65):
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(seconds * fraction),
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=480:-2",
                        "-threads",
                        "1",
                        str(cover),
                    ],
                    capture_output=True,
                    **child_options(),
                    timeout=35,
                )
                preview = QImage(str(cover)).scaled(16, 9)
                if (
                    not preview.isNull()
                    and sum(
                        preview.pixelColor(x, y).lightness() > 25 for x in range(16) for y in range(9)
                    )
                    > 12
                ):
                    break
        name = re.sub(r"^\d{14}-|[-－]视频-\d+-共享屏幕$", "", path.stem)
        data = dict(
            id=media_id,
            title=name,
            duration=seconds,
            path=str(path),
            filename=path.name,
            width=video.get("width"),
            size=round(stat.st_size / 1048576),
        )
        info.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {**data, "image": thumbnail(cover, 480) if cover.exists() else ""}
