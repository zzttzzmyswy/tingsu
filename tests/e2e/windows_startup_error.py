"""Verify native diagnostics when a packaged Windows UI component goes missing or changes."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile


def check_dialog(app, env, expected):
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    api.FindWindowW.restype = wintypes.HWND
    api.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    api.FindWindowExW.restype = wintypes.HWND
    api.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    api.GetDlgItem.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    proc = subprocess.Popen([app], env=env)
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end:
            hwnd = api.FindWindowW("#32770", "听序启动失败")
            pid = wintypes.DWORD()
            if hwnd:
                api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid:
                break
            if proc.poll() is not None:
                raise AssertionError(f"Application exited without a dialog ({proc.returncode})")
            time.sleep(0.1)
        else:
            raise AssertionError("No startup error dialog")
        child = None
        messages = []
        while child := api.FindWindowExW(hwnd, child, "Static", None):
            buf = ctypes.create_unicode_buffer(4096)
            api.GetWindowTextW(child, buf, len(buf))
            messages.append(buf.value)
        assert any(expected in s and "tingsu-webview.exe" in s for s in messages), messages
        ok_button = api.GetDlgItem(hwnd, 1)  # IDOK
        assert ok_button
        assert api.PostMessageW(ok_button, 0x00F5, 0, 0)  # BM_CLICK on the actual button
        assert proc.wait(timeout=10) == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    app = parser.parse_args().app.resolve()
    with zipfile.ZipFile(app) as archive:
        expected = hashlib.sha256(archive.read("bin/tingsu-webview.exe")).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, LOCALAPPDATA=tmp)
        subprocess.run([app, "--version"], env=env, timeout=30, check=True)
        (component,) = Path(tmp).glob("Tingsu/runtime/*/bin/tingsu-webview.exe")
        for replacement in (None, b"modified test fixture"):
            if replacement is None:
                component.unlink()
            else:
                component.write_bytes(replacement)
            check_dialog(app, env, expected)
            if replacement is None:
                assert not component.exists()
            else:
                assert component.read_bytes() == replacement
    print("Missing and changed UI components both produced native diagnostics.")


if __name__ == "__main__":
    main()
