#!/bin/sh
# Run tests/e2e/wlroots_window_capture.py in a private PID / network / mount namespace.
#
# Same containment as the KDE wrapper: its own /proc, its own loopback, its own
# runtime dir, and the namespace dies with this shell, so no host service, desktop
# or user data can be reached or cleaned up by name.
#
#   tests/e2e/wlroots_window_capture_isolated.sh \
#       --app dist/Tingsu-Linux-x86_64.AppImage --label wlroots --out /path/report \
#       --labwc /path/labwc --labwc-lib /path/lib --wtype /path/wtype \
#       --pointer /path/wlrpointer
set -eu
here=$(dirname "$(readlink -f "$0")")
project=$(dirname "$(dirname "$here")")

app=""; out=""; label="wlroots"; labwc=""; labwc_lib=""; wtype=""; pointer=""
while [ $# -gt 0 ]; do
    case "$1" in
        --app) app=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        --label) label=$2; shift 2 ;;
        --labwc) labwc=$2; shift 2 ;;
        --labwc-lib) labwc_lib=$2; shift 2 ;;
        --wtype) wtype=$2; shift 2 ;;
        --pointer) pointer=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$app" ] && [ -n "$labwc" ] && [ -n "$wtype" ] && [ -n "$pointer" ] || {
    echo "usage: $0 --app <AppImage> --out <dir> --labwc <labwc> --wtype <wtype> --pointer <wlrpointer>" >&2
    exit 2
}
out=$(readlink -f "${out:-$project/dist/reports}")
app=$(readlink -f "$app")
labwc=$(readlink -f "$labwc"); wtype=$(readlink -f "$wtype"); pointer=$(readlink -f "$pointer")
[ -n "$labwc_lib" ] && labwc_lib=$(readlink -f "$labwc_lib")
mkdir -p "$out"

host_pid_ns=$(readlink /proc/self/ns/pid)
echo "[isolation] host pid namespace   : $host_pid_ns"
echo "[isolation] host mount namespace : $(readlink /proc/self/ns/mnt)"

# labwc, its wlroots, wtype and wlr-protocols come from a prefix the caller
# prepared; every one of those paths is mounted read-only.
tools=$(dirname "$(dirname "$labwc")")
bind=""
for path in "$labwc" "$wtype" "$pointer"; do
    bind="$bind --ro-bind $path $path"
done
[ -n "$labwc_lib" ] && bind="$bind --ro-bind $labwc_lib $labwc_lib"
[ -d "$tools/share" ] && bind="$bind --ro-bind $tools/share $tools/share"

# shellcheck disable=SC2086
exec bwrap \
    --die-with-parent \
    --unshare-pid --unshare-ipc --unshare-net --unshare-uts --unshare-cgroup-try \
    --clearenv --proc /proc --dev-bind /dev /dev \
    --ro-bind /usr /usr --ro-bind /etc /etc \
    --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/bin /sbin \
    --ro-bind /sys /sys \
    --tmpfs /tmp --tmpfs /run --tmpfs /var --tmpfs /home \
    --bind "$out" "$out" \
    --ro-bind "$app" "$app" \
    --ro-bind "$here" "$here" \
    $bind \
    --setenv PATH /usr/bin \
    --setenv LANG C.UTF-8 --setenv LC_ALL C.UTF-8 \
    --setenv USER zzt --setenv HOME /home/qa \
    --setenv TMPDIR /tmp \
    --setenv TINGSU_HOST_PID_NS "$host_pid_ns" \
    --setenv XDG_SESSION_TYPE wayland --setenv XDG_CURRENT_DESKTOP wlr \
    --setenv QT_QPA_PLATFORM wayland \
    --setenv APPIMAGE_EXTRACT_AND_RUN 1 \
    --chdir "$here" \
    -- python3 -u "$here/wlroots_window_capture.py" \
        --app "$app" --label "$label" --out "$out" \
        --labwc "$labwc" ${labwc_lib:+--labwc-lib "$labwc_lib"} \
        --wtype "$wtype" --pointer "$pointer"
