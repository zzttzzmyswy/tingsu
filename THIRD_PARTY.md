# Third-party software and source downloads

Tingsu's application source is distributed under the [project license](LICENSE). Release packages include notices for the Go runtime, linked Go modules, and separately licensed native programs and shared libraries.

| Component | Distribution and corresponding source |
| --- | --- |
| Go runtime and SQLite | The Go backend links the standard library and the pure Go SQLite port. Release notices include each module's selected version. [Go](https://go.dev/dl/) · [Module versions and upstreams](go.mod) · [SQLite port](https://gitlab.com/cznic/sqlite) |
| Gio and UI font | Both builds link Gio and embed a subset of Noto Sans CJK SC. Module licenses and the font’s SIL Open Font License are included. [Gio](https://gioui.org/) · [Font license](internal/desktop/fonts/OFL.txt) |
| Meeting exports | HTML uses Goldmark. PDF uses go-weasyprint with the embedded Noto Sans CJK subset converted to TrueType outlines for text embedding. [Goldmark](https://github.com/yuin/goldmark) · [go-weasyprint](https://github.com/benoitkugler/go-weasyprint) |
| Linux media platform | GStreamer, ALSA, PulseAudio, X11 and general-purpose libraries come from Ubuntu 22.04. Native screen capture uses the desktop's ScreenCast portal and a PipeWire remote. Ubuntu component notices, versions and links to the corresponding sources are included in the AppImage. [Ubuntu package sources](https://launchpad.net/ubuntu/+sources) |
| Linux PipeWire audio client | The audio tools and matching client libraries come from Debian bookworm-backports 1.4.2-1~bpo12+1. [Exact Debian source](https://snapshot.debian.org/package/pipewire/1.4.2-1~bpo12+1/) |
| AppImage runtime | The type-2 runtime is distributed with its license. [Corresponding source](https://github.com/AppImage/type2-runtime/tree/fedfad51acc2860b2916a1eef32ba4a500ff31ef) |

Linux packages extract with `--appimage-extract`. The Windows executable stores a ZIP of native resources after its Go executable and installs it under `%LOCALAPPDATA%\Tingsu\runtime`. Windows uses the system WASAPI and Win32 interfaces. The repository contains the shell and backend sources, native build recipes, notices and assembly scripts. [Linux packaging](packaging/linux/README.md) · [Windows packaging](packaging/windows/README.md)
