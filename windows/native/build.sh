#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p ../../meeting_agent/native
"${CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -s -static -municode -DUNICODE -D_UNICODE \
    -Wl,--no-insert-timestamp main.cpp audio.cpp window.cpp -o ../../meeting_agent/native/tingsu-capture.exe \
    -lmmdevapi -luuid -lole32 -lksuser -lruntimeobject -ld3d11 -ldwmapi -lgdi32 -luser32
"${CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -s -static -municode -DUNICODE -D_UNICODE \
    -Wl,--no-insert-timestamp host.cpp -o ../../meeting_agent/native/tingsu-host.exe
