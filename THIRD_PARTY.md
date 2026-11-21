# Third-party software and source downloads

Tingsu's application source is distributed under the [project license](LICENSE). Release packages include notices for the Go runtime, linked Go modules, and separately licensed native programs and shared libraries.

| Component | Distribution and corresponding source |
| --- | --- |
| Go runtime and SQLite | The Go backend links the standard library and the pure Go SQLite port. Release notices include each module's selected version. [Go](https://go.dev/dl/) · [Module versions and upstreams](go.mod) · [SQLite port](https://gitlab.com/cznic/sqlite) |
| Gio and UI font | Both builds link Gio and embed a subset of Noto Sans CJK SC. Module licenses and the font’s SIL Open Font License are included. [Gio](https://gioui.org/) · [Font license](internal/desktop/fonts/OFL.txt) |
| Linux media platform | GStreamer, ALSA, PulseAudio, X11 and general-purpose libraries come from Ubuntu 22.04. Native screen capture uses the desktop's ScreenCast portal and a PipeWire remote. Ubuntu component notices, versions and links to the corresponding sources are included in the AppImage. [Ubuntu package sources](https://launchpad.net/ubuntu/+sources) |
| Linux PipeWire audio client | The audio tools and matching client libraries come from Debian bookworm-backports 1.4.2-1~bpo12+1. [Exact Debian source](https://snapshot.debian.org/package/pipewire/1.4.2-1~bpo12+1/) |
| AppImage runtime | The type-2 runtime is distributed with its license. [Corresponding source](https://github.com/AppImage/type2-runtime/tree/fedfad51acc2860b2916a1eef32ba4a500ff31ef) |
| FFmpeg and ffprobe | Both platforms build shared libraries from unmodified FFmpeg 9.1, LGPL 2.1 or later. Each platform's shell script gives the configuration, including the enabled application output formats. [Source tarball](https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz) · [Linux build](packaging/linux/build-media.sh) · [Windows build](packaging/windows/build-media.sh) |
| AV1 and DEFLATE support | dav1d 1.5.1 supports software AV1 decoding. Windows also compiles zlib 1.3.2. [dav1d source](https://download.videolan.org/pub/videolan/dav1d/1.5.1/dav1d-1.5.1.tar.xz) · [zlib source](https://zlib.net/fossils/zlib-1.3.2.tar.gz) |
| Windows compiler runtime | MinGW-w64 winpthreads and GCC runtime DLLs provide clocks and unwinding. Notices include the GCC Runtime Library Exception. [MinGW-w64 v14](https://github.com/mingw-w64/mingw-w64/tree/v14.0.0) · [GCC 16.2](https://gcc.gnu.org/releases.html) |

Linux packages extract with `--appimage-extract`. The Windows executable stores a ZIP of native resources after its Go executable and installs it under `%LOCALAPPDATA%\Tingsu\runtime`. Both layouts allow replacement of media shared libraries. The repository contains the shell and backend sources, native build recipes, notices and assembly scripts. [Linux packaging](packaging/linux/README.md) · [Windows packaging](packaging/windows/README.md)
