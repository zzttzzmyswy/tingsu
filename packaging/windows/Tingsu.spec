# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from importlib.metadata import distribution
from pathlib import Path


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
          (str(here / "licenses"), "licenses/webview"),
          (str(root / "THIRD_PARTY.md"), "licenses/tingsu"),
          (str(root / "packaging" / "sources"), "licenses/tingsu/packaging/sources"),
          (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/python")]
native_tools = [("bin/tingsu-capture.exe", str(helper), "DATA"),
                ("bin/tingsu-host.exe", str(helper.with_name("tingsu-host.exe")), "DATA"),
                ("bin/tingsu-webview.exe", str(helper.with_name("tingsu-webview.exe")), "DATA"),
                ("bin/WebView2Loader.dll", str(helper.with_name("WebView2Loader.dll")), "DATA")]
for asset in ("bin/ffmpeg.exe", "bin/ffprobe.exe", "licenses/ffmpeg/LICENSE"):
    path = vendor / asset
    if not path.is_file():
        raise SystemExit(f"Missing FFmpeg distribution file: {path}")
    native_tools.append((asset, str(path), "DATA"))

for pkg in ("PyInstaller",
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
    excludes=["tkinter", "gi", "PySide6", "shiboken6"],
)

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
