#!/bin/sh
set -eu
base=$(dirname "$(readlink -f "$0")")
cc -O2 -Wall -Wextra -Wno-unused-parameter "$base/main.c" -o "$1" \
  $(pkg-config --cflags --libs gio-unix-2.0 gstreamer-app-1.0 gstreamer-video-1.0)
strip "$1"
