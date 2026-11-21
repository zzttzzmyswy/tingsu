#!/bin/sh
# Finish a staged, Python-free AppDir. The backend and UI are refreshed each time.
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
project=$(dirname "$(dirname "$build_dir")")
appdir=$(readlink -f "$1")
cache=$2
destination=${TINGSU_DIST_DIR:-"$project/dist"}
cd "$project"
mkdir -p "$appdir/usr/bin" "$destination"
"$build_dir/build-gui.sh" "$appdir/usr/bin/tingsu"
install -m 755 "$build_dir/AppRun" "$appdir/AppRun"
install -m 644 "$build_dir/tingsu.desktop" "$appdir/tingsu.desktop"
test -x "$appdir/usr/bin/tingsu-portal"
install -m 644 "$project/meeting_agent/ui/tingsu.svg" "$appdir/tingsu.svg"
rsvg-convert -w 128 -h 128 "$appdir/tingsu.svg" > "$appdir/.DirIcon"
licenses="$appdir/usr/share/doc/tingsu/third-party"
python3 "$project/packaging/go-notices.py" "$licenses/go"
install -m 644 "$project/LICENSE" "$project/THIRD_PARTY.md" "$appdir/usr/share/doc/tingsu/"
install -m 644 "$build_dir/THIRD-PARTY.md" "$licenses/"
install -m 644 "$cache/runtime-LICENSE" "$licenses/"
"$build_dir/install-pw-client.sh" "$appdir" "$cache"
install -m 644 "$project/internal/desktop/fonts/OFL.txt" "$licenses/ui-font.txt"
# Keep the Go renderer on the desktop graphics stack. Only helper processes
# should load the packaged multimedia libraries.
mkdir -p "$appdir/usr/lib/tingsu/tools"
for tool in xwininfo tingsu-portal pactl parec arecord pw-record pw-dump; do
    case "$tool" in
      pw-record|pw-dump) continue ;; # Their launchers select a private PipeWire ABI.
    esac
    if ! cmp -s "$appdir/usr/bin/$tool" "$build_dir/launch-media"; then
        mv "$appdir/usr/bin/$tool" "$appdir/usr/lib/tingsu/tools/$tool"
    fi
    install -m 755 "$build_dir/launch-media" "$appdir/usr/bin/$tool"
done
if find "$appdir" \( -iname 'libpython*.so*' -o -type f -name 'python3.[0-9]*' -o -name '*.py' -o -name '*.pyc' \) -print | grep -q .; then
  echo "Python runtime found in AppDir" >&2
  exit 1
fi
if find "$appdir/usr/bin" "$appdir/usr/lib" -type f \( -name ffmpeg -o -name ffprobe -o -name 'libavcodec.so*' -o -name 'libavformat.so*' -o -name 'libavdevice.so*' -o -name 'libavfilter.so*' -o -name 'libavutil.so*' -o -name 'libswscale.so*' -o -name 'libswresample.so*' \) -print | grep -q .; then
  echo "Obsolete FFmpeg components in AppDir. Build from a clean staging directory." >&2
  exit 1
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
mksquashfs "$appdir" "$work/payload" -noappend -all-root -no-progress -comp zstd -Xcompression-level 19 -b 1M -processors 8
image="$destination/Tingsu-Linux-x86_64.AppImage"
temporary=$(mktemp "$destination/.tingsu-appimage.XXXXXX")
cat "$cache/runtime" "$work/payload" > "$temporary"
if [ "$(wc -c < "$temporary")" -ge 100000000 ]; then
    rm -f "$temporary"
    echo "Linux single-file application exceeds the 100 MB limit" >&2
    exit 1
fi
chmod 755 "$temporary"
mv "$temporary" "$image"
sha256sum "$image"
