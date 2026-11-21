# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths

here = Path(SPECPATH)
root = here.parent.parent
vendor = Path(os.environ["TINGSU_WINDOWS_VENDOR"])
helper = root / "meeting_agent" / "native" / "tingsu-capture.exe"

if not helper.is_file():
    raise SystemExit(f"Build the Windows capture helper first: {helper}")

# Utilities in this directory are complete Windows distributions. Treat them
# as data to preserve the original signatures and their native directory layout.
assets = [(str(root / "meeting_agent" / "ui"), "meeting_agent/ui"),
          (str(root / "LICENSE"), "licenses/tingsu"),
          (str(root / "THIRD_PARTY.md"), "licenses/tingsu"),
          (str(root / "packaging" / "sources"), "licenses/tingsu/packaging/sources"),
          (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/python")]
native_tools = [("bin/tingsu-capture.exe", str(helper), "DATA"),
                ("bin/tingsu-host.exe", str(helper.with_name("tingsu-host.exe")), "DATA")]
for asset in ("bin/ffmpeg.exe", "bin/ffprobe.exe", "licenses/ffmpeg/LICENSE"):
    path = vendor / asset
    if not path.is_file():
        raise SystemExit(f"Missing FFmpeg distribution file: {path}")
    native_tools.append((asset, str(path), "DATA"))

for pkg in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "PyInstaller",
            "certifi", "charset-normalizer", "idna", "urllib3", "requests", "pypdf",
            "httpcore", "httpx", "h11", "anyio", "typing-extensions"):
    dist = distribution(pkg)
    for member in dist.files or ():
        name = member.name.upper()
        if ".dist-info/" in str(member).replace("\\", "/") and (
            name.startswith(("LICENSE", "COPYING", "NOTICE")) or "licenses" in member.parts
        ):
            located = Path(dist.locate_file(member))
            if located.is_file():
                assets.append((str(located), f"licenses/{pkg}/" + str(member.parent)))

analysis = Analysis(
    [str(here / "entry.py")],
    pathex=[str(root)],
    datas=assets,
    hiddenimports=["pypdf"],
    excludes=["tkinter", "gi"],
)

# Qt and QtWebEngineProcess both load the MSVC runtime on a clean Windows
# installation. Ship the runtime supplied by the official Qt wheel app-locally.
qt_dir = Path(get_package_paths("PySide6")[1])
runtime_prefixes = ("msvcp140", "vcruntime140", "concrt140")
runtimes = sorted(p for p in qt_dir.glob("*.dll") if p.name.lower().startswith(runtime_prefixes))
if not any(p.name.lower() == "msvcp140.dll" for p in runtimes):
    raise SystemExit("The selected Qt wheel does not supply the application-local MSVC runtime")
crt_names = {p.name.lower() for p in runtimes}
analysis.binaries = [entry for entry in analysis.binaries if entry[0].lower() not in crt_names]
analysis.binaries.extend((p.name, str(p), "BINARY") for p in runtimes)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, native_tools, [],
    name="Tingsu-Windows-x86_64",
    icon=str(here / "tingsu.ico"),
    version=str(here / "version.txt"),
    console=False,
    debug=False,
    strip=False,
    upx=False,
)
