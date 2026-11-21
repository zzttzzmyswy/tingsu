"""Cross-build the Win32 WebView2 host with MinGW and pinned upstream headers."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SDK = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.2903.40/microsoft.web.webview2.1.0.2903.40.nupkg"
SDK_HASH = "ef128016dd1e51c59178c827ed5b8aa3322c57afa8675d930f8109505542ad74"
JSON_HEADER = "https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp"
JSON_HASH = "9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6"


def cached(cache, name, url, digest):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        data = urllib.request.urlopen(url, timeout=120).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit("Digest mismatch for " + name)
        path.write_bytes(data)
    return path


def build(cache):
    sdk = cached(cache, "webview2.nupkg", SDK, SDK_HASH)
    cpp_json = cached(cache, "json.hpp", JSON_HEADER, JSON_HASH)
    bin_path = ROOT / "meeting_agent/native/tingsu-webview.exe"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(sdk) as z:
        directory = Path(tmp)
        (bin_path.parent / "WebView2Loader.dll").write_bytes(z.read("build/native/x64/WebView2Loader.dll"))
        notices = ROOT / "packaging/windows/licenses"
        notices.mkdir(exist_ok=True)
        for name in ("LICENSE.txt", "NOTICE.txt"):
            (notices / ("WebView2-" + name)).write_bytes(z.read(name))
        header = z.read("build/native/include/WebView2.h").decode()
        (directory / "WebView2.h").write_text(header, encoding="utf-8")
        # Microsoft's generated COM declarations rely on MSVC's uuid metadata.
        # MinGW uses the same SDK interface identifiers through __CRT_UUID_DECL.
        guids = []
        for identifier, name in re.findall(r'MIDL_INTERFACE\("([a-fA-F0-9-]+)"\)\s+(\w+)\s*:', header):
            data = uuid.UUID(identifier)
            parts = [data.time_low, data.time_mid, data.time_hi_version, *data.bytes[8:]]
            guids.append(f'__CRT_UUID_DECL({name}, ' + ', '.join(hex(n) for n in parts) + ')')
        (directory / "webview2_guids.h").write_text('\n'.join(guids))
        (directory / "EventToken.h").write_text('#include <eventtoken.h>\n')
        (directory / "json.hpp").write_bytes(cpp_json.read_bytes())
        rc = directory / "icon.rc"
        rc.write_text(f'1 ICON "{(ROOT / "packaging/windows/tingsu.ico").as_posix()}"')
        icon = directory / "icon.o"
        subprocess.run([os.getenv("WINDRES","x86_64-w64-mingw32-windres"), str(rc), str(icon)], check=True)
        subprocess.run([os.getenv("CXX","x86_64-w64-mingw32-g++"), "-std=c++17", "-O2", "-s", "-static",
                        "-municode", "-mwindows", "-DUNICODE", "-D_UNICODE", "-Wl,--no-insert-timestamp",
                        "-I", str(directory), str(HERE / "webview.cpp"), str(icon), "-o", str(bin_path),
                        "-lshlwapi","-lcomdlg32","-luuid","-lole32","-lshell32","-luser32"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home()/".cache/tingsu-windows")
    build(parser.parse_args().cache)
