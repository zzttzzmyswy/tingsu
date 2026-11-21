"""Stage native audio and screen-sharing tools in a relocatable AppDir."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

dest = Path(sys.argv[1])
usr = dest / "usr"
prefix = usr / "lib/tingsu"
vendor = prefix / "native"
media = Path("/opt/tingsu-media")
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
    if graphics_abi.match(name) or name == "libreadline.so.8":
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
        package_lib(path, name)


def install_licenses():
    licenses.mkdir(parents=True, exist_ok=True)
    for pkg in Path("/usr/share/doc").iterdir():
        notice = pkg / "copyright"
        if notice.is_file():
            copy(notice, licenses / "ubuntu" / pkg.name / "copyright")
    tree("/usr/share/common-licenses", licenses / "common-licenses")
    versions = subprocess.check_output(["dpkg-query", "-W", "-f", "${binary:Package}\t${Version}\n"])
    (licenses / "ubuntu-package-versions.tsv").write_bytes(versions)
    sources = subprocess.check_output(["dpkg-query", "-W", "-f",
        "${binary:Package}\t${source:Package}\t${source:Version}\n"], text=True)
    from urllib.parse import quote
    with (licenses / "ubuntu-source-versions.tsv").open("w") as out:
        for row in sources.splitlines():
            _, name, version = row.split("\t")
            out.write(row + "\thttps://launchpad.net/ubuntu/+source/" + quote(name) + "/" + quote(version) + "\n")


for tool in tools:
    path = media / "bin" / tool if tool in ("ffmpeg", "ffprobe") else Path(shutil.which(tool))
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
# The portal is a PipeWire client. Read its two shipped client profiles so
# their module selections, including optional real-time support, stay in sync.
modules = set()
# libpipewire and module-adapter also request these factories directly.
spa_plugins = {
    "support/libspa-dbus", "support/libspa-journal", "audiomixer/libspa-audiomixer",
    "control/libspa-control", "videoconvert/libspa-videoconvert",
}
for name in ("client.conf", "client-rt.conf"):
    config = Path("/usr/share/pipewire") / name
    copy(config, usr / "share/pipewire" / name)
    rules = re.sub(r"#.*", "", config.read_text())
    modules.update(re.findall(r"\bname\s*=\s*(libpipewire-module-[\w-]+)", rules))
    spa_plugins.update(re.findall(r"=\s*([\w-]+/libspa-[\w-]+)", rules))
if "libpipewire-module-protocol-native" not in modules or "support/libspa-support" not in spa_plugins:
    raise RuntimeError("Unrecognized PipeWire client configuration")
for module in sorted(modules):
    file = multiarch / "pipewire-0.3" / (module + ".so")
    copy(file, prefix / "pipewire" / file.name, binary=True)
for plugin in sorted(spa_plugins):
    name = plugin + ".so"
    copy(multiarch / "spa-0.2" / name, prefix / "spa" / name, binary=True)
for path in Path("/etc/pulse").glob("*.conf"):
    copy(path, usr / "share/tingsu/pulse" / path.name)
tree("/usr/share/alsa", usr / "share/alsa")
for path in (multiarch / "libpulsecommon-15.99.so",):
    if not path.exists():
        path = next(multiarch.rglob("libpulsecommon-*.so"))
    copy(path, vendor / path.name, binary=True)

tree(media / "licenses", licenses / "ffmpeg")
copy(Path(sys.argv[2]), usr / "bin/tingsu-portal", binary=True)
include_dependencies(elf_sources)
install_licenses()
