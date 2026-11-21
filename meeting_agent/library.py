import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

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


def _image_bytes(path, *options):
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-frames:v", "1",
             "-filter_threads", "1", "-threads", "1", *options, "pipe:1"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10, **child_options(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return result.stdout if result.returncode == 0 else b""


def thumbnail(path, width=640):
    jpeg = _image_bytes(path, "-vf", f"scale={width}:{width}:force_original_aspect_ratio=decrease",
                        "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe")
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii") if jpeg else ""


def _has_picture(path):
    rgb = _image_bytes(path, "-vf", "scale=16:9", "-pix_fmt", "rgb24", "-f", "rawvideo")
    if len(rgb) != 16 * 9 * 3:
        return False
    pixels = zip(rgb[0::3], rgb[1::3], rgb[2::3])
    return sum(max(pixel) + min(pixel) > 50 for pixel in pixels) > 12


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
                if _has_picture(cover):
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
