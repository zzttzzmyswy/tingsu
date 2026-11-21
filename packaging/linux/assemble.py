"""Stage a relocatable Qt/Python desktop runtime from the clean Ubuntu builder."""

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
qt = wheel / "PySide6/Qt"
qtout = python / "PySide6/Qt"
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
    if source.is_relative_to(qt / "lib"):
        out = qtout / "lib" / name
    elif source.is_relative_to(wheel):
        out = python / source.relative_to(wheel)
    elif graphics_abi.match(name) or name == "libreadline.so.8":
        # Match the desktop driver and its loader from the same distribution.
        out = prefix / "graphics-fallback" / name / name
    else:
        out = vendor / name
    if not out.exists():
        copy(source, out)


def include_dependencies(objects, *, fallback=None):
    env = {
        **os.environ,
        "LD_LIBRARY_PATH": str(qt / "lib")
        + ":"
        + str(wheel / "PySide6")
        + ":"
        + str(wheel / "shiboken6"),
    }
    required = set()
    for obj in objects:
        result = subprocess.run(
            ["ldd", str(obj)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
        )
        if "=> not found" in result.stdout:
            raise RuntimeError(f"Unresolved dependency in {obj}:\n{result.stdout}")
        for line in result.stdout.splitlines():
            match = re.match(r"\s*(\S+) => (/\S+)", line)
            if match and not system.match(match[1]):
                required.add((match[1], match[2]))
    for name, path in sorted(required):
        if fallback is not None:
            if (
                not (vendor / name).exists()
                and not (prefix / "graphics-fallback" / name / name).exists()
            ):
                copy(path, fallback / name)
        else:
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
        path.name == "PySide6"
        or path.name in ("bin", "include", "__pycache__")
        or path.name.endswith(".dist-info")
    ):
        continue
    if path.is_dir():
        tree(path, python / path.name, ignore=("*.pyi", "include", "typesystems", "doc", "scripts"))
        elf_sources += list(path.rglob("*.so"))
    elif path.suffix == ".py":
        copy(path, python / path.name)
pyside = wheel / "PySide6"
module_names = (
    "QtCore",
    "QtGui",
    "QtWidgets",
    "QtNetwork",
    "QtPrintSupport",
    "QtOpenGL",
    "QtOpenGLWidgets",
    "QtWebEngineCore",
    "QtWebEngineWidgets",
    "QtWebChannel",
    "QtQuick",
    "QtQml",
    "QtQuickWidgets",
    "QtPositioning",
    "QtDBus",
    "QtSvg",
)
for name in module_names:
    path = pyside / (name + ".abi3.so")
    if not path.exists():
        path = next(pyside.glob(name + ".*.so"))
    copy(path, python / "PySide6" / path.name, binary=True)
for path in pyside.glob("*.py"):
    copy(path, python / "PySide6" / path.name)
tree(pyside / "support", python / "PySide6/support")
for path in pyside.glob("lib*.so*"):
    copy(path, python / "PySide6" / path.name, binary=True)
for category in (
    "platforms",
    "platforminputcontexts",
    "wayland-shell-integration",
    "wayland-graphics-integration-client",
    "wayland-decoration-client",
    "xcbglintegrations",
    "imageformats",
    "iconengines",
    "tls",
    "networkinformation",
    "platformthemes",
):
    for so in (qt / "plugins" / category).glob("*.so"):
        if so.name in (
            "libqeglfs.so",
            "libqvkkhrdisplay.so",
            "libqvnc.so",
            "libqtslibplugin.so",
            "libqminimal.so",
            "libqminimalegl.so",
            "libqlinuxfb.so",
            "libqgtk3.so",
            "libqpdf.so",
        ):
            continue
        copy(so, qtout / "plugins" / category / so.name, binary=True)
copy(qt / "libexec/QtWebEngineProcess", qtout / "libexec/QtWebEngineProcess", binary=True)
tree(qt / "resources", qtout / "resources")
for language in ("en-US", "zh-CN", "zh-TW"):
    path = qt / "translations/qtwebengine_locales" / (language + ".pak")
    copy(path, qtout / "translations/qtwebengine_locales" / path.name)
for path in (qt / "translations").glob("*_zh_CN.qm"):
    copy(path, qtout / "translations" / path.name)
for file in Path("/usr/share/fonts/opentype/noto").glob("NotoSansCJK-*.ttc"):
    copy(file, usr / "share/fonts/noto" / file.name)
for library in ("libnssckbi.so", "libsoftokn3.so", "libfreeblpriv3.so", "libfreebl3.so"):
    so = multiarch / "nss" / library
    copy(so, vendor / so.name, binary=True)
    check = so.with_suffix(".chk")
    if check.exists():
        copy(check, vendor / check.name)
include_dependencies(elf_sources)
software_gl = prefix / "graphics-software"
mesa = [multiarch / "libGLX_mesa.so.0", multiarch / "libEGL_mesa.so.0", multiarch / "dri/swrast_dri.so"]
for library in mesa[:2]:
    copy(library, software_gl / "lib" / library.name)
copy(mesa[2], software_gl / "drivers" / mesa[2].name)
include_dependencies(mesa, fallback=software_gl / "lib")
copy("/usr/share/glvnd/egl_vendor.d/50_mesa.json", software_gl / "50_mesa.json")
install_licenses()
