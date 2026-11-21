#!/bin/sh
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
root=$(dirname "$(dirname "$build_dir")")
cache=${TINGSU_BUILD_CACHE:-${XDG_CACHE_HOME:-"$HOME/.cache"}/tingsu-build/linux}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
"$build_dir/fetch-assets.sh" "$cache"
docker build -t tingsu-go-linux-builder:22.04 "$build_dir"
docker run --rm -v "$work:/work" -v "$cache:/assets:ro" -v "$build_dir:/build:ro" -v "$root/desktop:/desktop:ro" \
  -e BUILD_UID="$(id -u)" -e BUILD_GID="$(id -g)" tingsu-go-linux-builder:22.04 sh -ec '
  /build/build-media.sh /assets
  /desktop/portal/build.sh /work/tingsu-portal
  LD_LIBRARY_PATH=/opt/tingsu-media/lib python3 /build/assemble.py /work/AppDir /work/tingsu-portal
  /desktop/linux/build.sh /work/AppDir/usr/bin/tingsu-webview-bin
  chown -R "$BUILD_UID:$BUILD_GID" /work
'
"$build_dir/finish.sh" "$work/AppDir" "$cache"
