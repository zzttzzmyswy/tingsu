"""Browser-based Codex sign-in using the locally installed CLI."""

import asyncio
from concurrent.futures import Future
import re
import threading
import webbrowser

from .local_cli import LocalCLI


class CodexLogin:
    def __init__(self, *, post, finished, link_available):
        self.post = post
        self.finished = finished
        self.link_available = link_available
        self.attempt = None
        self.thread = None
        self.url = None

    def start(self, executable=""):
        self.stop()
        cli = LocalCLI("codex", executable)
        # Surface an invalid installation before the desktop enters login mode.
        cli.invocation("login")
        self.attempt = attempt = Future()
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run(cli, attempt)), daemon=True,
                                       name="tingsu-codex-login")
        self.thread.start()

    async def _run(self, cli, attempt):
        proc = reader = None
        cancel = asyncio.wrap_future(attempt)
        success = False
        try:
            proc = await cli.spawn("login", stdin=asyncio.subprocess.DEVNULL,
                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

            async def read():
                while line := await proc.stdout.readline():
                    match = re.search(rb"https://auth\.openai\.com/[^\s\x1b]+", line)
                    if match:
                        url = match[0].decode("ascii", "replace")
                        self.post(lambda value=url: self._set_url(attempt, value))
                return await proc.wait() == 0

            reader = asyncio.create_task(read())
            done, _ = await asyncio.wait([reader, cancel], timeout=600, return_when=asyncio.FIRST_COMPLETED)
            success = reader in done and reader.result()
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            if reader:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if proc and proc.returncode is None:
                await cli.stop(proc)
            cancel.cancel()
            self.post(lambda: self._completed(attempt, success))

    def _set_url(self, attempt, url):
        if self.attempt is attempt:
            self.url = url
            self.link_available()

    def _completed(self, attempt, success):
        if self.attempt is attempt:
            self.attempt, self.url, self.thread = None, None, None
            self.finished(success)

    def open_browser(self):
        if self.url is None or not webbrowser.open(self.url):
            raise ValueError("尚未获得可打开的登录链接，请稍后再试。")

    def stop(self):
        attempt = self.attempt
        thread = self.thread
        self.attempt, self.url, self.thread = None, None, None
        if attempt:
            attempt.cancel()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
