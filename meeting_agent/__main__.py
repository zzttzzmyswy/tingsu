import asyncio
from contextlib import ExitStack, contextmanager
import faulthandler
import os
import sys

from .bridge import Bridge
from .desktop import DesktopHost, start_shell
from .runtime import app_directory
from .storage import DATA, private_dir


@contextmanager
def workspace_lock():
    # The OS releases the lock even when a native process crashes.
    path = private_dir(DATA) / "desktop.instance"
    with path.open("a+b") as file:
        if os.name == "nt":
            import msvcrt
            if file.tell() == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            def lock():
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            def lock():
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            lock()
        except (PermissionError, BlockingIOError):
            yield False
        else:
            yield True


def main():
    os.umask(0o077)
    with ExitStack() as cleanup:
        acquired = cleanup.enter_context(workspace_lock())
        try:
            path = private_dir(app_directory("cache")) / "desktop-fault.log"
            if path.exists() and path.stat().st_size > 512 * 1024:
                path.replace(path.with_suffix(".previous.log"))
            fault_log = cleanup.enter_context(path.open("ab"))
            faulthandler.enable(file=fault_log, all_threads=True)
            cleanup.callback(faulthandler.disable)
        except OSError:
            pass
        loop = asyncio.new_event_loop()
        cleanup.callback(loop.close)
        host = DesktopHost(start_shell(), loop)
        if acquired:
            host.bridge = Bridge(host)
        return host.run()


if __name__ == "__main__":
    sys.exit(main())
