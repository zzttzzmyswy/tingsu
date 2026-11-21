#!/bin/sh
set -eu
here=$(dirname "$(readlink -f "$0")")
if pkg-config --exists webkit2gtk-4.1; then sdk=webkit2gtk-4.1; else sdk=webkit2gtk-4.0; fi
# Only the headers come from the WebKit SDK; load the matching 4.1 or 4.0 SONAME on the user's desktop.
c++ -std=c++17 -O2 -Wall -Wextra -Wno-deprecated-declarations \
    $(pkg-config --cflags gtk+-3.0 gio-unix-2.0 json-glib-1.0 "$sdk") "$here/main.cpp" -o "$1" \
    $(pkg-config --libs gtk+-3.0 gio-unix-2.0 json-glib-1.0) -ldl
strip "$1"
