#!/bin/sh
# Finish a staged, Python-free AppDir. The backend and UI are refreshed each time.
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
project=$(dirname "$(dirname "$build_dir")")
appdir=$(readlink -f "$1")
cache=$2
destination=${TINGSU_DIST_DIR:-"$project/dist"}
cd "$project"
mkdir -p "$appdir/usr/share/tingsu/ui" "$destination"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -ldflags="-s -w" -o "$appdir/usr/bin/tingsu" ./cmd/tingsu
install -m 755 "$build_dir/AppRun" "$appdir/AppRun"
install -m 644 "$build_dir/tingsu.desktop" "$appdir/tingsu.desktop"
test -x "$appdir/usr/bin/tingsu-webview-bin"
test -x "$appdir/usr/bin/tingsu-portal"
install -m 755 "$build_dir/launch-webview" "$appdir/usr/bin/tingsu-webview"
install -m 644 "$project"/meeting_agent/ui/*.js "$project"/meeting_agent/ui/*.html \
    "$project"/meeting_agent/ui/*.css "$project"/meeting_agent/ui/*.svg "$appdir/usr/share/tingsu/ui/"
install -m 644 "$project/meeting_agent/ui/tingsu.svg" "$appdir/tingsu.svg"
rsvg-convert -w 128 -h 128 "$appdir/tingsu.svg" > "$appdir/.DirIcon"
licenses="$appdir/usr/share/doc/tingsu/third-party"
python3 "$project/packaging/go-notices.py" "$licenses/go"
install -m 644 "$project/LICENSE" "$project/THIRD_PARTY.md" "$appdir/usr/share/doc/tingsu/"
mkdir -p "$appdir/usr/share/doc/tingsu/packaging/sources"
install -m 644 "$project"/packaging/sources/ubuntu-22.04.tsv "$appdir/usr/share/doc/tingsu/packaging/sources/"
install -m 644 "$build_dir/THIRD-PARTY.md" "$licenses/"
install -m 644 "$cache/runtime-LICENSE" "$licenses/"
"$build_dir/install-pw-client.sh" "$appdir" "$cache"
if find "$appdir" \( -iname 'libpython*.so*' -o -type f -name 'python3.[0-9]*' -o -name '*.py' -o -name '*.pyc' \) -print | grep -q .; then
  echo "Python runtime found in AppDir" >&2
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
