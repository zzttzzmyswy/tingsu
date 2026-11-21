"""Cross-build (or build natively) the single x64 Windows desktop executable."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", required=True, type=Path, help="Prefix produced by build-media.sh")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    args.dist.mkdir(parents=True, exist_ok=True)
    media = args.media.resolve()
    with tempfile.TemporaryDirectory(dir=args.dist) as temp:
        work = Path(temp)
        env = dict(os.environ, CGO_ENABLED="0", GOOS="windows", GOARCH="amd64")
        exe = work / "Tingsu-Windows-x86_64.exe"
        windres = shutil.which("x86_64-w64-mingw32-windres") or "windres"
        # Go links .syso resources by their actual paths. Build a private copy of the
        # entry package inside the module so concurrent platforms need no shared writes.
        with tempfile.TemporaryDirectory(prefix=".windows-build-", dir=ROOT) as src:
            package = Path(src)
            for path in (ROOT / "cmd/tingsu").glob("*.go"):
                shutil.copyfile(path, package / path.name)
            resource = package / "resources_windows_amd64.syso"
            subprocess.run([windres, "--target=pe-x86-64", "resources.rc", "-O", "coff", "-o", resource], cwd=HERE, check=True)
            subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w -H=windowsgui",
                            "-o", exe.resolve(), "./" + package.name], env=env, cwd=ROOT, check=True)
        notices = work / "go"
        subprocess.run([sys.executable, HERE.parent / "go-notices.py", notices.resolve()], cwd=ROOT, check=True)
        def tree(folder, prefix):
            return [(p, prefix + "/" + p.relative_to(folder).as_posix()) for p in folder.rglob("*") if p.is_file()]
        files = tree(ROOT / "meeting_agent/ui", "ui") + tree(HERE / "licenses", "licenses/native")
        files += tree(notices, "licenses/go") + tree(media / "licenses", "licenses/ffmpeg")
        files += [(ROOT / "LICENSE", "licenses/tingsu/LICENSE"), (ROOT / "THIRD_PARTY.md", "licenses/tingsu/THIRD_PARTY.md"),
                  (HERE / "build-media.sh", "licenses/ffmpeg/build-media.sh")]
        for f in ["ffmpeg.exe", "ffprobe.exe", "avcodec-63.dll", "avdevice-63.dll", "avfilter-12.dll", "avformat-63.dll",
                  "avutil-61.dll", "swresample-7.dll", "swscale-10.dll"]:
            files.append((media / "bin" / f, "bin/" + f))
        for path in (media / "bin").glob("lib*.dll"):
            files.append((path, "bin/" + path.name))
        for f in ["tingsu-capture.exe", "tingsu-host.exe", "tingsu-webview.exe", "WebView2Loader.dll"]:
            files.append((ROOT / "meeting_agent/native" / f, "bin/" + f))
        for name in ("ffmpeg-LGPL-2.1.txt", "dav1d.txt", "zlib.txt"):
            if not (media / "licenses" / name).is_file():
                raise ValueError("Missing FFmpeg distribution notice: " + name)
        payload = work / "resources.zip"
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for path, name in sorted(files, key=lambda entry: entry[1]):
                entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                entry.external_attr = 0o100644 << 16
                z.writestr(entry, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        data = payload.read_bytes()
        with exe.open("ab") as f:
            f.write(data)
            f.write(hashlib.sha256(data).digest())
            f.write(struct.pack("<Q", len(data)))
            f.write(b"TINGSU-ZIP-v1")
        if exe.stat().st_size >= 100_000_000:
            raise ValueError("Windows single executable exceeds 100 MB")
        exe.replace(args.dist / exe.name)
        print(f"{len(data):,} bytes of compressed resources")

if __name__ == "__main__":
    main()
