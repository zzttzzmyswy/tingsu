# Linux runtime components

| Component | Upstream and licensing |
| --- | --- |
| CPython, PyGObject, GStreamer, PipeWire, PulseAudio, ALSA, FFmpeg and their native libraries | Ubuntu 22.04 binary packages. Individual notices are in `ubuntu/` and `common-licenses/`. Build package versions are in `ubuntu-package-versions.tsv`. The source inventory is in `packaging/sources/ubuntu-22.04.tsv` in the repository. |
| PipeWire 1.4.2 audio recording client | Debian bookworm-backports package version 1.4.2-1~bpo12+1. Copyright notices are in `debian/`. [Matching upstream source and packaging](https://deb.debian.org/debian/pool/main/p/pipewire/) |
| GTK 3, GLib, JSON-GLib, WebKitGTK and JavaScriptCoreGTK | The user's desktop provides these shared libraries. The in-tree C++ shell dynamically loads WebKitGTK 4.1 or 4.0. [GTK licensing](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/COPYING) · [WebKit licensing](https://webkit.org/licensing-webkit/) |
| Requests, urllib3, idna, charset-normalizer, certifi, httpx, httpcore, h11, anyio, exceptiongroup, typing-extensions and pypdf | The Python wheels' notices and distribution metadata are in `wheels/`. |
| AppImage Type 2 runtime | See `runtime-LICENSE`. [Runtime project](https://github.com/AppImage/type2-runtime) |

The AppImage can be unpacked with `--appimage-extract`. Bundled shared libraries are under `usr/lib/tingsu/native`. The supplied packaging scripts assemble the image from individually replaceable libraries.
