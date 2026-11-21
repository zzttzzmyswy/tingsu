#!/bin/sh
# Jammy's pw-cat lacks raw PCM recording and its pw-dump emits invalid JSON on newer servers.
set -eu
build_dir=$(dirname "$(readlink -f "$0")")
appdir=$1
cache=$2
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
for package in pipewire-bin libpipewire-0.3-0 libpipewire-0.3-modules libspa-0.2-modules; do
    dpkg-deb -x "$cache/$package.deb" "$stage"
done
bundle="$appdir/usr/lib/tingsu/pw-client"
multi="$stage/usr/lib/x86_64-linux-gnu"
mkdir -p "$bundle/bin" "$bundle/lib" "$bundle/modules" "$bundle/config" "$bundle/spa"
install -m 755 "$stage/usr/bin/pw-cat" "$bundle/bin/pw-record"
install -m 755 "$stage/usr/bin/pw-dump" "$bundle/bin/pw-dump"
install -m 755 "$multi/libpipewire-0.3.so.0" "$bundle/lib/"
install -m 644 "$stage/usr/share/pipewire/client.conf" "$bundle/config/"
for module in rt protocol-native client-node client-device adapter metadata session-manager; do
    install -m 644 "$multi/pipewire-0.3/libpipewire-module-$module.so" "$bundle/modules/"
done
for plugin in support audioconvert; do
    mkdir -p "$bundle/spa/$plugin"
    install -m 644 "$multi/spa-0.2/$plugin/libspa-$plugin.so" "$bundle/spa/$plugin/"
done
for cmd in pw-dump pw-record; do
    install -m 755 "$build_dir/pw-client" "$appdir/usr/bin/$cmd"
done
for package in pipewire-bin libpipewire-0.3-0 libpipewire-0.3-modules libspa-0.2-modules; do
    install -Dm644 "$stage/usr/share/doc/$package/copyright" \
        "$appdir/usr/share/doc/tingsu/third-party/debian/$package/copyright"
done
