"""Build the portable x64 Windows release with a Windows Python interpreter."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CODEX_RELEASE = "https://github.com/openai/codex/releases/download/rust-v0.171.0"
CODEX_SOURCE = "https://raw.githubusercontent.com/openai/codex/rust-v0.171.0"
FFMPEG_RELEASE = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.1-essentials_build.zip"


def download(cache, name, url, expected):
    target = cache / name

    def matches(path):
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == expected

    if not matches(target):
        print(f"Downloading {name}", flush=True)
        temp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url, timeout=90) as stream, temp.open("wb") as out:
            shutil.copyfileobj(stream, out)
        if not matches(temp):
            temp.unlink(missing_ok=True)
            raise SystemExit(f"SHA-256 mismatch for {name}")
        temp.replace(target)
    return target


def prepare(cache, vendor):
    ffmpeg = download(
        cache,
        "ffmpeg-9.1.zip",
        FFMPEG_RELEASE,
        "b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd",
    )
    codex = download(
        cache,
        "codex-0.171.0.tar.gz",
        CODEX_RELEASE + "/codex-package-x86_64-pc-windows-msvc.tar.gz",
        "669941eff23dd8470fbf85d919a03d80c4e3d35b441e3fcd361c0f8630945be8",
    )
    with tarfile.open(codex, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            destination = (vendor / member.name).resolve()
            if not destination.is_relative_to(vendor):
                raise ValueError("Unexpected Codex archive member")
            destination.parent.mkdir(exist_ok=True, parents=True)
            with archive.extractfile(member) as stream, destination.open("wb") as out:
                shutil.copyfileobj(stream, out)
    with zipfile.ZipFile(ffmpeg) as archive:
        for filename in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
            member = next(path for path in archive.namelist() if path.endswith("/" + filename))
            folder = vendor / ("licenses/ffmpeg" if filename == "LICENSE" else "bin")
            folder.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as stream, (folder / filename).open("wb") as out:
                shutil.copyfileobj(stream, out)
    for name, digest in (
        ("LICENSE", "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc"),
        ("NOTICE", "9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915"),
    ):
        origin = download(cache, "codex-" + name, CODEX_SOURCE + "/" + name, digest)
        folder = vendor / "licenses/codex"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, folder / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Tingsu-Build"
    )
    parser.add_argument(
        "--vendor", type=Path, help="Use an already extracted copy of the pinned upstream tools"
    )
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("Use a 64-bit Windows Python interpreter.")
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    vendor = (args.vendor or cache / "vendor-9.1-0.171.0").resolve()
    if not args.vendor:
        prepare(cache, vendor)
    for asset in (
        "bin/ffmpeg.exe",
        "bin/ffprobe.exe",
        "bin/codex.exe",
        "licenses/ffmpeg/LICENSE",
        "licenses/codex/NOTICE",
    ):
        if not (vendor / asset).is_file():
            raise SystemExit(f"Missing upstream asset: {vendor / asset}")
    env = dict(os.environ, TINGSU_WINDOWS_VENDOR=str(vendor))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(args.dist.resolve()),
            "--workpath",
            str(cache / "pyinstaller"),
            str(HERE / "Tingsu.spec"),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
