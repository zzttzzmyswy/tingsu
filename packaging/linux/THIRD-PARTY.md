# Runtime components

| Component | Upstream and licensing |
| --- | --- |
| CPython, PyGObject, GStreamer, PipeWire, PulseAudio, ALSA, FFmpeg, Fontconfig, Noto CJK and native libraries | Ubuntu 22.04 binary packages. Individual notices and license texts are in `ubuntu/` and `common-licenses/`. Exact package versions are in `ubuntu-package-versions.tsv`. Ubuntu source packages at https://archive.ubuntu.com/ubuntu/pool/ are identified by the source names in the corresponding copyright records. |
| PipeWire 1.4.2 audio recording client | Debian bookworm-backports package version 1.4.2-1~bpo12+1. Copyright notices in `debian/`. Matching source and packaging at https://deb.debian.org/debian/pool/main/p/pipewire/ |
| PySide6, Shiboken6 and Qt | Qt for Python 6.12.0 and Qt WebEngine 6.12.0.140. See https://www.qt.io/licensing/open-source-lgpl-obligations and https://code.qt.io/cgit/pyside/pyside-setup.git/. The GNU LGPL v3 and GPL texts are in `common-licenses/`. Qt module sources are available at https://code.qt.io/cgit/qt/. Chromium third-party notices are included in the WebEngine resources. |
| Requests, urllib3, idna, charset-normalizer, certifi, httpx, httpcore, h11, anyio, exceptiongroup, typing-extensions and pypdf | The Python wheels' notices are preserved in `wheels/`. Installed versions and distribution metadata are listed alongside the license files. |
| OpenAI Codex CLI 0.171.0 | Apache License 2.0. See `codex-LICENSE` and `codex-NOTICE`. https://github.com/openai/codex/releases/tag/rust-v0.171.0 |
| AppImage Type 2 runtime | See `runtime-LICENSE` and https://github.com/AppImage/type2-runtime. |

The AppImage can be unpacked with `--appimage-extract`. Shared libraries are under `usr/lib/tingsu/native` and `usr/lib/tingsu/python/PySide6/Qt/lib`. The supplied packaging scripts assemble the image from individually replaceable libraries.
