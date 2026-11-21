#!/bin/sh
# Called inside the Jammy builder. Keep all FFmpeg demuxers and built-in decoders.
set -eu
cache=$1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 -m tarfile -e "$cache/ffmpeg-9.1.tar.xz" "$work"
python3 -m tarfile -e "$cache/dav1d-1.5.1.tar.xz" "$work"
cd "$work/dav1d-1.5.1"
meson setup build --prefix=/opt/tingsu-media --libdir=lib --buildtype=release --default-library=shared -Denable_tools=false -Denable_tests=false
ninja -C build -j"${JOBS:-8}" install
cd "$work/ffmpeg-9.1"
export PKG_CONFIG_PATH=/opt/tingsu-media/lib/pkgconfig
./configure --prefix=/opt/tingsu-media --enable-shared --disable-static --disable-autodetect --disable-debug --disable-doc --disable-ffplay \
  --enable-zlib --enable-libdav1d --enable-libxcb --enable-libxcb-shm --enable-alsa --enable-libpulse \
  --disable-encoders --enable-encoder=mjpeg,pcm_s16le,png,rawvideo,wrapped_avframe --disable-network \
  --disable-filters --enable-filter=aformat,anull,aresample,atrim,format,hflip,null,scale,setpts,transpose,trim,vflip \
  --disable-muxers --enable-muxer=image2,image2pipe,mjpeg,null,rawvideo,segment,pcm_s16le,wav
make -j"${JOBS:-8}"
make install
mkdir -p /opt/tingsu-media/licenses
cp COPYING.LGPLv2.1 /opt/tingsu-media/licenses/ffmpeg-LGPL-2.1.txt
cp "$work/dav1d-1.5.1/COPYING" /opt/tingsu-media/licenses/dav1d.txt
