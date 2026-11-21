"""Capture the window or monitor selected in the XDG ScreenCast chooser."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

PORTAL = "org.freedesktop.portal.Desktop"
BASE = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
SESSION = "org.freedesktop.portal.Session"
REQUEST = "org.freedesktop.portal.Request"


class _Portal:
    """Use a private D-Bus connection so teardown also releases outstanding requests."""

    def __init__(self, Gio, GLib, context, stop: threading.Event):
        self.Gio, self.GLib = Gio, GLib
        self.context = context
        self.stop = stop
        self.closed = False
        self.session = None
        flags = Gio.DBusConnectionFlags
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address, flags.AUTHENTICATION_CLIENT | flags.MESSAGE_BUS_CONNECTION, None, None
        )
        self.bus.set_exit_on_close(False)

    def call(self, path, interface, method, args=None):
        return self.bus.call_sync(
            PORTAL, path, interface, method, args, None, self.Gio.DBusCallFlags.NONE, 5000, None
        )

    def pump(self):
        while self.context.pending():
            self.context.iteration(False)
        if self.stop.is_set():
            raise InterruptedError
        if self.closed or self.bus.is_closed():
            raise RuntimeError("桌面已结束本次画面共享。")

    def request(self, method, signature, prefix=(), options=None):
        variant = self.GLib.Variant
        token = "meeting_" + uuid.uuid4().hex
        args = variant(signature, (*prefix, {**(options or {}), "handle_token": variant("s", token)}))
        response = {}

        def on_response(connection, sender, path, iface, name, params, *unused):
            response[path] = params.unpack()

        # Older backends can return a different path. Subscribe before making
        # the call, then match the actual returned handle (also covers fast replies).
        sub = self.bus.signal_subscribe(
            PORTAL, REQUEST, "Response", None, None, self.Gio.DBusSignalFlags.NONE, on_response
        )
        path = None
        try:
            (path,) = self.call(BASE, SCREENCAST, method, args).unpack()
            while path not in response:
                self.pump()
                self.stop.wait(0.035)
            status, result = response[path]
            if status == 1:
                raise RuntimeError("已取消选择共享画面。")
            if status:
                raise RuntimeError("桌面门户未能开始共享，请检查当前桌面的 ScreenCast 后端。")
            return result
        finally:
            self.bus.signal_unsubscribe(sub)
            if path and path not in response:
                self.close_handle(path, REQUEST)

    def close_handle(self, path, iface):
        if self.bus.is_closed():
            return
        try:
            self.call(path, iface, "Close")
        except self.GLib.Error:
            pass  # The session may have been closed by the compositor first.

    def select_source(self):
        variant = self.GLib.Variant
        result = self.request(
            "CreateSession",
            "(a{sv})",
            options={"session_handle_token": variant("s", "session_" + uuid.uuid4().hex)},
        )
        self.session = result["session_handle"]

        def on_closed(*args):
            self.closed = True

        self.bus.signal_subscribe(
            PORTAL, SESSION, "Closed", self.session, None, self.Gio.DBusSignalFlags.NONE, on_closed
        )
        (supported,) = self.call(
            BASE,
            "org.freedesktop.DBus.Properties",
            "Get",
            variant("(ss)", (SCREENCAST, "AvailableSourceTypes")),
        ).unpack()
        types = supported & 3  # Monitor and window. wlroots may only advertise monitor.
        if not types:
            raise RuntimeError("桌面门户没有可共享的窗口或显示器。")
        self.request(
            "SelectSources",
            "(oa{sv})",
            (self.session,),
            {"types": variant("u", types), "multiple": variant("b", False)},
        )
        result = self.request("Start", "(osa{sv})", (self.session, ""))
        streams = result.get("streams", [])
        if not streams:
            raise RuntimeError("桌面门户未返回视频流。")
        node, properties = streams[0]
        return node, properties.get("pipewire-serial")

    def remote_fd(self):
        handle, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL,
            BASE,
            SCREENCAST,
            "OpenPipeWireRemote",
            self.GLib.Variant("(oa{sv})", (self.session, {})),
            None,
            self.Gio.DBusCallFlags.NONE,
            5000,
            None,
            None,
        )
        return fds.get(handle.unpack()[0])

    def close(self):
        if self.session:
            self.close_handle(self.session, SESSION)
        if not self.bus.is_closed():
            self.bus.close_sync(None)


class ScreenCapture:
    """One-shot portal session. start() selects a source and delivers its first JPEG.

    snapshot() requests another current JPEG, including when sharing a static
    slide. Both callbacks run on the capture thread. stop() is nonblocking.
    """

    def __init__(
        self, on_frame: Callable[[Path], None], on_error: Callable[[str], None], output_dir: Path | str
    ):
        self.on_frame = on_frame
        self.on_error = on_error
        self.output_dir = Path(output_dir)
        self._stop = threading.Event()
        self._wanted = threading.Event()
        self._wanted.set()
        self._condition = threading.Condition()
        self._latest = None
        self._finished = False
        self._window = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="meeting-screen")

    def start(self) -> None:
        if os.name == "nt":
            from .windows_capture import choose_window

            self._window = choose_window()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def snapshot(self) -> None:
        self._wanted.set()

    def snapshot_sync(self, timeout: float = 12) -> Path:
        """Obtain a new frame for an agent turn. Call this only on a worker thread."""
        with self._condition:
            previous = self._latest
            self.snapshot()
            ready = self._condition.wait_for(
                lambda: self._latest != previous or self._finished or self._stop.is_set(), timeout
            )
            if self._stop.is_set() or self._finished:
                raise RuntimeError("画面共享已结束。")
            if not ready:
                raise TimeoutError("等待共享画面超时，请检查窗口选择或重新共享。")
            return self._latest

    def join(self, timeout: float | None = None) -> None:
        if self._thread.ident is not None:
            self._thread.join(timeout)

    def _write(self, image: bytes):
        with tempfile.NamedTemporaryFile(
            dir=self.output_dir, prefix="screen-", suffix=".jpg", delete=False
        ) as dest:
            dest.write(image)
            path = Path(dest.name)
        with self._condition:
            self._latest = path
            self._condition.notify_all()
        self.on_frame(path)

    def _portal_frames(self, Gst, portal, node, serial):
        with ExitStack() as cleanup:
            fd = portal.remote_fd()
            cleanup.callback(os.close, fd)
            pipeline = Gst.parse_launch(
                "pipewiresrc name=desktop ! video/x-raw ! identity drop-buffer-flags=corrupted "
                "! appsink name=frames sync=false max-buffers=1 drop=true enable-last-sample=false"
            )
            cleanup.callback(pipeline.set_state, Gst.State.NULL)
            # A compositor can timestamp the first damaged frame in its absolute
            # monotonic clock domain. Stills do not need a playback clock.
            pipeline.use_clock(None)
            source = pipeline.get_by_name("desktop")
            source.set_property("fd", fd)
            if source.find_property("always-copy"):
                source.set_property("always-copy", True)
            if serial is not None and source.find_property("target-object"):
                source.set_property("target-object", str(serial))
            else:
                source.set_property("path", str(node))
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("无法启动 PipeWire 视频流。")
            self._read_frames(Gst, pipeline, portal)

    def _read_frames(self, Gst, pipeline, portal):
        import gi

        gi.require_version("GstVideo", "1.0")
        from gi.repository import GstVideo

        deadline = time.monotonic() + 15
        sink = pipeline.get_by_name("frames")
        bus = pipeline.get_bus()
        jpeg = Gst.Caps.from_string("image/jpeg")
        latest = None
        ready_at = 0.0
        while not self._stop.is_set():
            portal.pump()
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg:
                if msg.type == Gst.MessageType.ERROR:
                    raise RuntimeError(msg.parse_error()[0].message)
                raise RuntimeError("共享窗口的视频流已结束。")
            sample = sink.emit("try-pull-sample", 120 * Gst.MSECOND)
            if sample is not None:
                if latest is None:
                    # Initial negotiation can yield a cleared buffer before
                    # the compositor paints the selected surface.
                    ready_at = time.monotonic() + 0.35
                latest = sample
            if latest is None and time.monotonic() > deadline:
                raise RuntimeError("等待共享窗口画面超时，请重新选择正在显示的窗口。")
            if latest is not None and self._wanted.is_set() and time.monotonic() >= ready_at:
                self._wanted.clear()
                converted = GstVideo.video_convert_sample(latest, jpeg, 3 * Gst.SECOND)
                if converted is None:
                    raise RuntimeError("无法将桌面画面转换为 JPEG。")
                buf = converted.get_buffer()
                if not self._stop.is_set():
                    self._write(buf.extract_dup(0, buf.get_size()))

    def _x11_command(self, args, timeout=None):
        deadline = time.monotonic() + timeout if timeout else None
        with subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C"},
        ) as proc:
            try:
                while not self._stop.is_set():
                    try:
                        data, errors = proc.communicate(timeout=0.15)
                    except subprocess.TimeoutExpired:
                        if deadline and time.monotonic() > deadline:
                            raise RuntimeError("等待 X11 窗口画面超时。")
                        continue
                    if proc.returncode:
                        raise RuntimeError(errors.decode(errors="replace").strip()[-500:])
                    return data
                raise InterruptedError
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def _x11_frames(self):
        # xwininfo releases its interactive pointer grab when stopped.
        result = self._x11_command(["xwininfo"])
        match = re.search(rb"Window id:\s*(0x[0-9a-fA-F]+)", result)
        if not match:
            raise RuntimeError("没有选中 X11 窗口。")
        window = match[1].decode()
        while not self._stop.is_set():
            if self._wanted.wait(0.15):
                self._wanted.clear()
                image = self._x11_command(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-nostdin",
                        "-loglevel",
                        "error",
                        "-f",
                        "x11grab",
                        "-window_id",
                        window,
                        "-draw_mouse",
                        "0",
                        "-i",
                        os.environ["DISPLAY"],
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale='min(iw,1920)':-2",
                        "-c:v",
                        "mjpeg",
                        "-f",
                        "image2pipe",
                        "pipe:1",
                    ],
                    timeout=8,
                )
                if not image:
                    raise RuntimeError("所选 X11 窗口没有可读取的画面。")
                if not self._stop.is_set():
                    self._write(image)

    def _run(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self._stop.is_set():
                return
            if os.name == "nt":
                from .windows_capture import capture_windows_frames

                capture_windows_frames(self._window, self._stop, self._wanted, self._write)
                return
            session_type = (
                os.environ.get("TINGSU_CAPTURE_DESKTOP")
                or os.environ.get("XDG_SESSION_TYPE")
                or ("wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11")
            )
            if session_type == "x11" and os.environ.get("DISPLAY"):
                self._x11_frames()
                return
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst

            Gst.init(None)
            with ExitStack() as cleanup:
                if self._stop.is_set():
                    return
                context = GLib.MainContext.new()
                context.push_thread_default()
                cleanup.callback(context.pop_thread_default)
                portal = _Portal(Gio, GLib, context, self._stop)
                cleanup.callback(portal.close)
                node, serial = portal.select_source()
                self._portal_frames(Gst, portal, node, serial)
        except Exception as error:
            if not self._stop.is_set():
                self.on_error(f"采集画面失败：{error}")
        finally:
            with self._condition:
                self._finished = True
                self._condition.notify_all()
