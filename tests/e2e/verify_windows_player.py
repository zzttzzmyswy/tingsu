"""Compare private Windows VLC capture evidence with the locally supplied recording."""

import argparse
import json
import subprocess
import tempfile
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
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(
            [
                "mpv",
                "--no-config",
                "--no-terminal",
                "--no-audio",
                "--untimed",
                "--vo=image",
                "--vo-image-format=png",
                "--vo-image-high-bit-depth=no",
                "--vo-image-outdir=" + temp,
                "--start=299",
                "--length=95",
                "--vf=fps=1,scale=256:-2",
                "--",
                recording,
            ],
            check=True,
            timeout=120,
        )
        frames = sorted(Path(temp).glob("*.png"))
        assert frames, "The reference player returned no frames"
        with Image.open(frames[0]) as im:
            w, h = im.size
        refs = []
        for frame in frames:
            with Image.open(frame) as im:
                refs.append(
                    np.asarray(im.convert("L").resize((128, 72)), dtype=float).ravel()
                )
        refs = np.stack(refs)
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
