# Linux runtime components

| Component | Upstream and licensing |
| --- | --- |
| GStreamer, PipeWire, PulseAudio, ALSA and system libraries | Ubuntu 22.04 binaries. Copyright notices are in ubuntu/ and common-licenses/. The exact build package versions and source package names are listed in ubuntu-package-versions.tsv and ubuntu-source-versions.tsv. |
| PipeWire 1.4.2 audio recording client | Debian bookworm-backports 1.4.2-1~bpo12+1. Notices are in debian/. [Corresponding source](https://snapshot.debian.org/package/pipewire/1.4.2-1~bpo12+1/) |
| Gio UI and Noto Sans CJK | The Go program links Gio and uses the host EGL/GLES and window system. The bundled font license is in ui-font.txt. |
| Go runtime and modules | Per-version licenses are in go/. The application links the Go SQLite package. |
| AppImage Type 2 runtime | The license is in runtime-LICENSE. [Source](https://github.com/AppImage/type2-runtime/tree/fedfad51acc2860b2916a1eef32ba4a500ff31ef) |

The AppImage can be extracted with `--appimage-extract`. Bundled multimedia shared libraries are under usr/lib/tingsu/native. The build scripts assemble the image from individually replaceable libraries.
