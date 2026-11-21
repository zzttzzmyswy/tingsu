#!/bin/sh
set -eu
cache=$1
mkdir -p "$cache"
download() {
    name=$1
    url=$2
    digest=$3
    if ! printf '%s  %s\n' "$digest" "$cache/$name" | sha256sum --check --status 2>/dev/null; then
        if command -v aria2c >/dev/null 2>&1; then
            aria2c --continue=true --allow-overwrite=true --auto-file-renaming=false \
                --file-allocation=none --max-tries=8 --retry-wait=2 --split=8 \
                --max-connection-per-server=8 --min-split-size=2M \
                --checksum="sha-256=$digest" --dir="$cache" --out="$name.part" "$url"
        else
            # Keep completed ranges when a large release download is interrupted.
            for attempt in 1 2 3 4; do
                if curl --fail --location --continue-at - --output "$cache/$name.part" "$url"; then
                    break
                fi
            done
        fi
        printf '%s  %s\n' "$digest" "$cache/$name.part" | sha256sum --check --status
        mv "$cache/$name.part" "$cache/$name"
    fi
}
pw_base=https://deb.debian.org/debian/pool/main/p/pipewire
pw_version=1.4.2-1~bpo12+1_amd64.deb
download pipewire-bin.deb "$pw_base/pipewire-bin_$pw_version" 1a20049cb95305f6510375bd84751c9296b26741655c819e07d6a165c9dce8dc
download libpipewire-0.3-0.deb "$pw_base/libpipewire-0.3-0_$pw_version" 9f084571f31ece972c7157b5f544343685fadbfe0752d498aea173e37ae03f3c
download libpipewire-0.3-modules.deb "$pw_base/libpipewire-0.3-modules_$pw_version" 788b2dc2349a4fecf1814d0df71d862537a8800904a646a0e5e841310980054d
download libspa-0.2-modules.deb "$pw_base/libspa-0.2-modules_$pw_version" 791380c7bcd699a34f8b6a3756db225ea167f0b1ad058eff2e0600670e81cb70
download runtime https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64 617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46
curl --fail --silent --show-error --location --retry 3 --output "$cache/runtime-LICENSE" https://raw.githubusercontent.com/AppImage/type2-runtime/fedfad51acc2860b2916a1eef32ba4a500ff31ef/LICENSE
download ffmpeg-9.1.tar.xz https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz 1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433
download dav1d-1.5.1.tar.xz https://download.videolan.org/pub/videolan/dav1d/1.5.1/dav1d-1.5.1.tar.xz 401813f1f89fa8fd4295805aa5284d9aed9bc7fc1fdbe554af4292f64cbabe21
