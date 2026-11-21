#!/bin/sh
# Run on x86_64 Linux with Docker, dpkg-deb, squashfs-tools, curl, tar, zstd and librsvg installed.
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
root=$(dirname "$(dirname "$build_dir")")
cache=${TINGSU_BUILD_CACHE:-${XDG_CACHE_HOME:-"$HOME/.cache"}/tingsu-build/linux}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
"$build_dir/fetch-assets.sh" "$cache"
docker build -t tingsu-linux-builder:22.04 "$build_dir"
docker run --rm --user "$(id -u):$(id -g)" -v "$work:/work" -v "$build_dir:/build:ro" \
    -v "$root/desktop/linux:/webview:ro" tingsu-linux-builder:22.04 sh -ec \
    'python3 /build/assemble.py /work/AppDir && /webview/build.sh /work/AppDir/usr/bin/tingsu-webview-bin'
"$build_dir/finish.sh" "$work/AppDir" "$cache"
