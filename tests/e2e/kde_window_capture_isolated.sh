#!/bin/sh
# Run tests/e2e/kde_window_capture.py in a private PID / network / mount namespace.
#
# The whole desktop session - dbus, PipeWire, KWin, the KDE portal, Chrome and the
# package under test - lives inside one bwrap namespace with its own /proc, its
# own loopback network and its own runtime dir. Nothing is signalled by name or
# pattern; the namespace dies with this shell, so no host user service, desktop,
# Chrome profile or meeting database can be touched.
#
#   tests/e2e/kde_window_capture_isolated.sh \
#       --app dist/Tingsu-Linux-x86_64.AppImage --label candidate --expect pass \
#       --out /path/to/report [--python-helper <dir with kdefakeinput+kdeshot.py>]
#
# Extra arguments are passed through to kde_window_capture.py.
set -eu
here=$(dirname "$(readlink -f "$0")")
project=$(dirname "$(dirname "$here")")
root=$(dirname "$project")

app=""; out=""; label=""; helpers=""; extra=""
while [ $# -gt 0 ]; do
    case "$1" in
        --app) app=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        --label) label=$2; shift 2 ;;
        --helpers) helpers=$2; shift 2 ;;
        *) extra="$extra $1"; shift ;;
    esac
done
[ -n "$app" ] || { echo "usage: $0 --app <AppImage> --label <name> --out <dir> [--helpers <dir>]" >&2; exit 2; }
[ -n "$label" ] || label=$(basename "$app" .AppImage)
out=$(readlink -f "${out:-$project/dist/reports}")
app=$(readlink -f "$app")
mkdir -p "$out"

# The KWin session needs its own screen reader and pointer client; tests/e2e
# ships neither, so the caller points at the built helpers.
if [ -z "$helpers" ]; then
    for candidate in "$root/kde-test/bin" "$project/tests/e2e/kde"; do
        [ -x "$candidate/kdefakeinput" ] && [ -f "$candidate/kdeshot.py" ] && helpers=$candidate
    done
fi
[ -n "$helpers" ] || { echo "no kdefakeinput/kdeshot.py found; pass --helpers" >&2; exit 2; }
helpers=$(readlink -f "$helpers")

host_pid_ns=$(readlink /proc/self/ns/pid)
host_mnt_ns=$(readlink /proc/self/ns/mnt)
echo "[isolation] host pid namespace   : $host_pid_ns"
echo "[isolation] host mount namespace : $host_mnt_ns"

set -- bwrap \
    --die-with-parent \
    --unshare-pid --unshare-ipc --unshare-net --unshare-uts --unshare-cgroup-try \
    --clearenv --proc /proc --dev-bind /dev /dev \
    --ro-bind /usr /usr --ro-bind /etc /etc \
    --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/bin /sbin \
    --ro-bind-try /opt/google/chrome /opt/google/chrome \
    --ro-bind /sys /sys \
    --tmpfs /tmp --tmpfs /run --tmpfs /var --tmpfs /home \
    --bind "$out" "$out" \
    --ro-bind "$app" "$app" \
    --ro-bind "$here" "$here" \
    --ro-bind "$helpers" "$helpers" \
    --setenv PATH /usr/bin \
    --setenv LANG C.UTF-8 --setenv LC_ALL C.UTF-8 \
    --setenv USER zzt --setenv HOME /home/qa \
    --setenv TMPDIR /tmp \
    --setenv TINGSU_HOST_PID_NS "$host_pid_ns" \
    --setenv TINGSU_HOST_MNT_NS "$host_mnt_ns" \
    --setenv XDG_SESSION_TYPE wayland --setenv XDG_CURRENT_DESKTOP KDE \
    --setenv QT_QPA_PLATFORM wayland \
    --setenv APPIMAGE_EXTRACT_AND_RUN 1 \
    --chdir "$here" \
    -- python3 -u "$here/kde_window_capture.py" \
        --app "$app" --label "$label" --out "$out" \
        --fakeinput "$helpers/kdefakeinput" --shot "$helpers/kdeshot.py" \
        --software-gl

# Word-splitting of $extra is intended: it carries plain --flag value pairs.
set -- "$@" $extra
exec "$@"
