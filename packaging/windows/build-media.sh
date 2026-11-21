#!/bin/sh
# Build a replaceable FFmpeg DLL set for Windows using x86_64 MinGW-w64.
set -eu
cache=${1:?cache directory}
out=${2:?installation prefix}
mkdir -p "$cache" "$out"
cache=$(cd "$cache" && pwd)
out=$(cd "$out" && pwd)
mode=${3:-shared}
case "$mode" in shared) linkage="--enable-shared --disable-static";; static) linkage="--disable-shared --enable-static";; *) exit 2;; esac
cd "$cache"
get() {
    if ! echo "$2  $1" | sha256sum -c --status 2>/dev/null; then
        curl -fsSL --retry 3 "$3" -o "$1.tmp"
        echo "$2  $1.tmp" | sha256sum -c --status
        mv "$1.tmp" "$1"
    fi
}
get ffmpeg-9.1.tar.xz 1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433 https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz
get dav1d-1.5.1.tar.xz 401813f1f89fa8fd4295805aa5284d9aed9bc7fc1fdbe554af4292f64cbabe21 https://download.videolan.org/pub/videolan/dav1d/1.5.1/dav1d-1.5.1.tar.xz
get zlib-1.3.2.tar.gz bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16 https://zlib.net/fossils/zlib-1.3.2.tar.gz
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for archive in ffmpeg-9.1.tar.xz dav1d-1.5.1.tar.xz zlib-1.3.2.tar.gz; do tar -xf "$archive" -C "$work"; done
cross=x86_64-w64-mingw32
cd "$work/zlib-1.3.2"
make -f win32/Makefile.gcc -j"${JOBS:-8}" PREFIX="$cross-" libz.a
mkdir -p "$work/deps/lib" "$work/deps/include"
cp libz.a "$work/deps/lib/"
cp zlib.h zconf.h "$work/deps/include/"
cat > "$work/cross.ini" <<CROSS
[binaries]
c = '$cross-gcc'
ar = '$cross-ar'
strip = '$cross-strip'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
CROSS
cd "$work/dav1d-1.5.1"
meson setup build --cross-file="$work/cross.ini" --prefix="$work/deps" --libdir=lib --buildtype=release --default-library=static -Denable_tools=false -Denable_tests=false
ninja -C build -j"${JOBS:-8}" install
cd "$work/ffmpeg-9.1"
export PKG_CONFIG_LIBDIR="$work/deps/lib/pkgconfig"
./configure --prefix="$out" --target-os=mingw32 --arch=x86_64 --cross-prefix="$cross-" \
  --pkg-config=pkg-config --pkg-config-flags=--static --extra-cflags="-I$work/deps/include" --extra-ldflags="-L$work/deps/lib" \
  --enable-libdav1d --enable-zlib $linkage --disable-autodetect --disable-debug --disable-doc --disable-ffplay \
  --disable-encoders --enable-encoder=pcm_s16le,mjpeg,png,rawvideo,wrapped_avframe --disable-network --disable-devices \
  --disable-filters --enable-filter=aformat,anull,aresample,atrim,format,hflip,null,scale,setpts,transpose,trim,vflip \
  --disable-muxers --enable-muxer=image2,image2pipe,mjpeg,null,rawvideo,segment,pcm_s16le,wav
make -j"${JOBS:-8}"
make install
mkdir -p "$out/licenses"
cp COPYING.LGPLv2.1 "$out/licenses/ffmpeg-LGPL-2.1.txt"
cp "$work/dav1d-1.5.1/COPYING" "$out/licenses/dav1d.txt"
cp "$work/zlib-1.3.2/LICENSE" "$out/licenses/zlib.txt"

# MinGW toolchains can provide POSIX clocks and unwind support in small DLLs.
# Discover runtime dependencies of this particular compiler and its selected binaries.
runtime_dir=$(dirname "$("$cross-gcc" -print-file-name=libwinpthread.dll.a)")/../bin
for name in libwinpthread-1.dll libgcc_s_seh-1.dll; do
    if "$cross-objdump" -p "$out"/bin/*.exe "$out"/bin/*.dll 2>/dev/null | grep -Fqi "DLL Name: $name"; then
        path=$("$cross-gcc" -print-file-name="$name")
        [ -f "$path" ] || path="$runtime_dir/$name"
        install -m 755 "$path" "$out/bin/$name"
    fi
done
