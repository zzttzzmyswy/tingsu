# Linux runtime components

| Component | Upstream and licensing |
| --- | --- |
| FFmpeg 9.1 and dav1d 1.5.1 | Source-built shared libraries. The ffmpeg/ directory contains LGPL and BSD notices. [FFmpeg](https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz) · [dav1d](https://download.videolan.org/pub/videolan/dav1d/1.5.1/dav1d-1.5.1.tar.xz) |
| GStreamer, PipeWire, PulseAudio, ALSA and system libraries | Ubuntu 22.04 binaries. Copyright notices are in ubuntu/ and common-licenses/. The exact build package versions and source package names are listed in ubuntu-package-versions.tsv and ubuntu-source-versions.tsv. |
| PipeWire 1.4.2 audio recording client | Debian bookworm-backports 1.4.2-1~bpo12+1. Notices are in debian/. [Corresponding source](https://snapshot.debian.org/package/pipewire/1.4.2-1~bpo12+1/) |
| GTK 3, JSON-GLib, WebKitGTK and JavaScriptCoreGTK | The desktop provides the WebView runtime libraries. The in-tree C++ shell dynamically loads WebKitGTK 4.1 or 4.0. |
| Go runtime and modules | Per-version licenses are in go/. The application links the Go SQLite package. |
| AppImage Type 2 runtime | The license is in runtime-LICENSE. [Source](https://github.com/AppImage/type2-runtime/tree/fedfad51acc2860b2916a1eef32ba4a500ff31ef) |

The AppImage can be extracted with `--appimage-extract`. Bundled multimedia shared libraries are under usr/lib/tingsu/native. The build scripts assemble the image from individually replaceable libraries.
