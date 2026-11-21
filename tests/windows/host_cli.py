"""Exercise the Windows launcher with a real executable in a Unicode path.

On Windows, run `py -3 tests/windows/host_cli.py` from the repository root.
Use --launcher to select a launcher extracted from a single-file release.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def fixture(mode, args):
    if mode == "echo":
        folder = ctypes.create_unicode_buffer(4096)
        ctypes.windll.kernel32.GetDllDirectoryW(len(folder), folder)
        data = sys.stdin.buffer.readline().decode("utf-8").rstrip("\r\n")
        print(json.dumps([args, os.getcwd(), folder.value, data]), flush=True)
        return 27
    marker = Path(args[0])
    if mode == "branch":
        leaf = subprocess.Popen(
            [sys.executable, __file__, "--fixture", "leaf", str(marker)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(json.dumps([os.getpid(), leaf.pid]), flush=True)
    else:
        marker.write_text(str(os.getpid()), encoding="ascii")
    time.sleep(90)
    return 0


def verify(launcher, root):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]

    install = root / "本地 CLI"
    install.mkdir()
    exe = install / "Test 客户端.exe"
    interpreter = Path(sys.base_prefix) / "python.exe"
    shutil.copy2(interpreter, exe)
    # The copied Windows interpreter needs its standard library and runtime DLL.
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    env["PATH"] = str(interpreter.parent) + os.pathsep + env.get("PATH", "")
    script = str(Path(__file__).resolve())

    def spawn(parent, mode, *args):
        return subprocess.Popen(
            [str(launcher), str(parent), str(exe), script, "--fixture", mode, *args],
            cwd=install, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    foreign = root / "模拟包内 DLL"
    foreign.mkdir()
    if not kernel.SetDllDirectoryW(str(foreign)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        args = ["alpha beta", 'x "y"', "ends-in-" + chr(92), "中文", ""]
        content = 'line with "quotes" 和空格'
        with spawn(os.getpid(), "echo", *args) as proc:
            raw, error = proc.communicate((content + "\n").encode(), timeout=20)
            assert proc.returncode == 27, (proc.returncode, error)
            actual, cwd, dll, message = json.loads(raw)
            assert actual == args and Path(cwd) == install and not dll and message == content
    finally:
        kernel.SetDllDirectoryW(None)

    print("Unicode executable, argument quoting, UTF-8 input, DLL search and exit code passed")
    for reason in ("cancel", "parent-exit"):
        marker = root / reason
        parent = None
        handles = []
        if reason == "parent-exit":
            parent = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(90)"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        broker = spawn(parent.pid if parent else os.getpid(), "branch", str(marker))
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() or not marker.stat().st_size:
                if broker.poll() is not None:
                    raise RuntimeError(broker.stderr.read().decode(errors="replace"))
                if time.monotonic() > deadline:
                    raise TimeoutError("Grandchild did not start")
                time.sleep(0.05)
            pids = json.loads(broker.stdout.readline())
            assert int(marker.read_text()) == pids[1]
            for pid in pids:
                handle = kernel.OpenProcess(0x100001, False, pid)  # synchronize + terminate
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
            if parent:
                parent.kill()
                parent.wait(timeout=10)
            else:
                broker.kill()
            broker.wait(timeout=10)
            for handle in handles:
                assert kernel.WaitForSingleObject(handle, 7000) == 0, reason
            print(reason, "released CLI and grandchild")
        finally:
            if parent and parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)
            if broker.poll() is None:
                broker.kill()
            for handle in handles:
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    kernel.TerminateProcess(handle, 1)
                kernel.CloseHandle(handle)
            broker.communicate(timeout=10)


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Run this test using a Windows x64 Python interpreter.")
    if sys.argv[1:2] == ["--fixture"]:
        sys.exit(fixture(sys.argv[2], sys.argv[3:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--launcher", type=Path,
        default=Path(__file__).resolve().parents[2] / "meeting_agent/native/tingsu-host.exe",
    )
    with tempfile.TemporaryDirectory(prefix="tingsu-host-") as name:
        verify(parser.parse_args().launcher.resolve(strict=True), Path(name))
