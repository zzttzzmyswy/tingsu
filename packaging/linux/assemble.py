"""Stage a relocatable Python/audio runtime from the clean Ubuntu builder."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

dest = Path(sys.argv[1])
usr = dest / "usr"
prefix = usr / "lib/tingsu"
python = prefix / "python"
vendor = prefix / "native"
wheel = Path("/opt/tingsu-python")
licenses = usr / "share/doc/tingsu/third-party"
multiarch = Path("/usr/lib/x86_64-linux-gnu")
tools = (
    "ffmpeg",
    "ffprobe",
    "xwininfo",
    "pw-record",
    "pw-dump",
    "pactl",
    "parec",
    "arecord",
)
# Keep the host graphics driver, C runtime and C++ runtime in control of its own ABI.
system = re.compile(
    r"^(?:ld-linux.*|lib(?:c|m|pthread|dl|rt|util|anl|resolv|gcc_s|stdc\+\+)\.so(?:\..*)?)$"
)
graphics_abi = re.compile(
    r"^lib(?:gbm|drm|GL|EGL|GLX|GLdispatch|wayland-[a-z]+|xcb(?:-[a-z0-9-]+)?|X11(?:-xcb)?)\.so\."
)
elf_sources = []


def copy(source, output, *, binary=False):
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    if binary:
        elf_sources.append(source)


def tree(source, output, ignore=()):
    shutil.copytree(
        source,
        output,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *ignore),
    )


def package_lib(source, alias=None):
    source = Path(source)
    name = alias or source.name
    if source.is_relative_to(wheel):
        out = python / source.relative_to(wheel)
    elif graphics_abi.match(name) or name == "libreadline.so.8":
        # Match the desktop driver and its loader from the same distribution.
        out = prefix / "graphics-fallback" / name / name
    else:
        out = vendor / name
    if not out.exists():
        copy(source, out)


def include_dependencies(objects):
    required = set()
    for obj in objects:
        result = subprocess.run(
            ["ldd", str(obj)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        if "=> not found" in result.stdout:
            raise RuntimeError(f"Unresolved dependency in {obj}:\n{result.stdout}")
        for line in result.stdout.splitlines():
            match = re.match(r"\s*(\S+) => (/\S+)", line)
            if match and not system.match(match[1]):
                required.add((match[1], match[2]))
    for name, path in sorted(required):
        package_lib(Path(path).resolve() if Path(path).is_relative_to(wheel) else path, name)


def install_licenses():
    licenses.mkdir(parents=True, exist_ok=True)
    for info in wheel.glob("*.dist-info"):
        for file in info.rglob("*"):
            if file.is_file() and (
                "license" in file.name.lower()
                or "notice" in file.name.lower()
                or "copying" in file.name.lower()
                or "licenses" in file.parts
                or file.name == "METADATA"
            ):
                copy(file, licenses / "wheels" / info.name / file.relative_to(info))
    # Apt and wheel license files are redistributable as shipped by their upstreams.
    for pkg in Path("/usr/share/doc").iterdir():
        notice = pkg / "copyright"
        if notice.is_file():
            copy(notice, licenses / "ubuntu" / pkg.name / "copyright")
    tree("/usr/share/common-licenses", licenses / "common-licenses")
    versions = subprocess.check_output(["dpkg-query", "-W", "-f", "${binary:Package}\t${Version}\n"])
    (licenses / "ubuntu-package-versions.tsv").write_bytes(versions)


copy("/usr/bin/python3.10", usr / "bin/python3.10", binary=True)
tree(
    "/usr/lib/python3.10",
    usr / "lib/python3.10",
    ignore=("test", "tests", "idlelib", "tkinter", "ensurepip", "lib2to3", "turtle*", "config-3.10-*"),
)
elf_sources += list(Path("/usr/lib/python3.10/lib-dynload").glob("*.so"))
for directory in ("gi",):
    root = Path("/usr/lib/python3/dist-packages") / directory
    tree(root, usr / "lib/python3/dist-packages" / directory, ignore=("*.pyi",))
    elf_sources += list(root.rglob("*.so"))
for typelib in (multiarch / "girepository-1.0").glob("*.typelib"):
    copy(typelib, prefix / "typelib" / typelib.name)
for tool in tools:
    path = Path(shutil.which(tool))
    copy(path, usr / "bin" / tool, binary=True)
for plugin in (
    "coreelements",
    "app",
    "videoconvert",
    "videoscale",
    "videorate",
    "jpeg",
    "typefindfunctions",
    "encoding",
    "playback",
    "pipewire",
):
    file = multiarch / "gstreamer-1.0" / f"libgst{plugin}.so"
    copy(file, prefix / "gstreamer" / file.name, binary=True)
scanner = next(Path("/usr/lib").rglob("gst-plugin-scanner"))
copy(scanner, prefix / "gst-plugin-scanner", binary=True)
for directory, name in (("pipewire-0.3", "pipewire"), ("spa-0.2", "spa")):
    root = multiarch / directory
    for path in root.rglob("*.so"):
        copy(path, prefix / name / path.relative_to(root), binary=True)
tree("/usr/share/pipewire", usr / "share/pipewire")
for path in Path("/etc/pulse").glob("*.conf"):
    copy(path, usr / "share/tingsu/pulse" / path.name)
tree("/usr/share/alsa", usr / "share/alsa")
for path in (multiarch / "libpulsecommon-15.99.so",):
    if not path.exists():
        path = next(multiarch.rglob("libpulsecommon-*.so"))
    copy(path, vendor / path.name, binary=True)

for path in wheel.iterdir():
    if (
        path.name in ("bin", "include", "__pycache__")
        or path.name.endswith(".dist-info")
    ):
        continue
    if path.is_dir():
        tree(path, python / path.name, ignore=("*.pyi", "include", "typesystems", "doc", "scripts"))
        elf_sources += list(path.rglob("*.so"))
    elif path.suffix == ".py":
        copy(path, python / path.name)
include_dependencies(elf_sources)
install_licenses()
