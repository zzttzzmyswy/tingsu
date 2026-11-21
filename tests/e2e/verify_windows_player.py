"""Compare private Windows VLC capture evidence with the locally supplied recording."""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from native_player_linux import validate_audio


def video_pixels(path, aspect):
    # The VLC WinGDI window includes a title bar and letterboxes the video.
    gray = Image.open(path).convert("L")
    pixels = np.asarray(gray)
    w, h = gray.size
    bright = np.where(pixels[h // 20 :, w // 8 : 7 * w // 8].mean(axis=1) > 12)[0]
    if not len(bright):
        raise ValueError("Captured player window is blank")
    center = (bright[0] + bright[-1]) / 2 + h // 20
    frame_height = min(int(w / aspect), h)
    top = min(max(0, round(center - frame_height / 2)), h - frame_height)
    return np.asarray(
        gray.crop((0, top, w, top + frame_height)).resize((128, 72)), dtype=float
    ).ravel()


def verify(recording, evidence):
    score = validate_audio(recording, evidence)
    files = sorted(evidence.glob("profile/Tingsu/data/frames/*/screen-*.jpg"))
    assert len(files) >= 2, "Expected at least two independently requested frames"
    dims = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            recording,
        ],
        text=True,
    )
    w, h = map(int, dims.strip().split(","))
    source = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "299",
            "-i",
            recording,
            "-t",
            "95",
            "-an",
            "-vf",
            "fps=1,scale=128:72",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    refs = np.frombuffer(source, dtype=np.uint8).reshape(-1, 128 * 72).astype(float)
    refs -= refs.mean(axis=1, keepdims=True)
    frames = []
    for f in files:
        a = video_pixels(f, w / h)
        a -= a.mean()
        correlations = (
            refs
            @ a
            / np.maximum(1e-9, np.linalg.norm(refs, axis=1) * np.linalg.norm(a))
        )
        frames.append(round(float(np.max(correlations)), 5))
    assert min(frames) > 0.75, frames
    return {
        "external_player": "VLC",
        "audio_correlation": score,
        "frame_correlations": frames,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.recording, args.evidence)))
