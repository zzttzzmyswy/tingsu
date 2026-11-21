#!/bin/sh
# Finish an already staged clean AppDir. Only project code and application UI are copied.
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
project=$(dirname "$(dirname "$build_dir")")
appdir=$1
cache=$2
destination=${TINGSU_DIST_DIR:-"$project/dist"}
runtime="$appdir/usr/lib/tingsu"
mkdir -p "$appdir/usr/share/tingsu" "$runtime/python/meeting_agent/ui" "$runtime/codex" "$destination"
install -m 755 "$build_dir/AppRun" "$appdir/AppRun"
install -m 644 "$build_dir/tingsu.desktop" "$appdir/tingsu.desktop"
install -m 644 "$build_dir/fonts.conf" "$appdir/usr/share/tingsu/fonts.conf"
install -m 644 "$build_dir/start.py" "$runtime/start.py"
install -m 644 "$project"/meeting_agent/*.py "$runtime/python/meeting_agent/"
install -m 644 "$project"/meeting_agent/ui/*.js "$project"/meeting_agent/ui/*.html \
    "$project"/meeting_agent/ui/*.css "$project"/meeting_agent/ui/*.svg "$runtime/python/meeting_agent/ui/"
install -m 644 "$project/meeting_agent/ui/tingsu.svg" "$appdir/tingsu.svg"
rsvg-convert -w 128 -h 128 "$appdir/tingsu.svg" > "$appdir/.DirIcon"
tar --zstd -xf "$cache/codex-package.tar.zst" -C "$runtime/codex"
test -x "$runtime/codex/bin/codex"
licenses="$appdir/usr/share/doc/tingsu/third-party"
install -m 644 "$project/LICENSE" "$project/THIRD_PARTY.md" "$appdir/usr/share/doc/tingsu/"
mkdir -p "$appdir/usr/share/doc/tingsu/packaging/sources"
install -m 644 "$project"/packaging/sources/* "$appdir/usr/share/doc/tingsu/packaging/sources/"
install -m 644 "$build_dir/THIRD-PARTY.md" "$licenses/"
install -m 644 "$cache/codex-LICENSE" "$cache/codex-NOTICE" "$cache/runtime-LICENSE" "$licenses/"
"$build_dir/install-pw-client.sh" "$appdir" "$cache"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
mksquashfs "$appdir" "$work/payload" -noappend -all-root -no-progress -comp zstd -Xcompression-level 12 -b 1M -processors 8
image="$destination/Tingsu-Linux-x86_64.AppImage"
temporary=$(mktemp "$destination/.tingsu-appimage.XXXXXX")
cat "$cache/runtime" "$work/payload" > "$temporary"
chmod 755 "$temporary"
mv "$temporary" "$image"
sha256sum "$image"
