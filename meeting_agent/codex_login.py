"""Browser-based Codex sign-in, with credentials managed by the native CLI."""

import re

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from .runtime import codex_binary


class CodexLogin(QObject):
    finished = Signal(bool)
    link_available = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.process = None
        self.url = None

    def start(self):
        self.stop()
        executable = codex_binary()
        if not executable:
            raise ValueError("未找到 Codex 可执行文件。")
        process = self.process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        partial = bytearray()

        def output():
            partial.extend(bytes(process.readAllStandardOutput()))
            while b"\n" in partial:
                line, _, tail = partial.partition(b"\n")
                partial[:] = tail
                match = re.search(rb"https://auth\.openai\.com/[^\s\x1b]+", line)
                if match and self.process is process:
                    self.url = QUrl.fromEncoded(bytes(match[0]))
                    self.link_available.emit()

        def end(success):
            if self.process is process:
                self.process, self.url = None, None
                self.finished.emit(success)
            process.deleteLater()

        process.readyReadStandardOutput.connect(output)
        process.finished.connect(
            lambda code, status: end(code == 0 and status == QProcess.ExitStatus.NormalExit)
        )
        process.errorOccurred.connect(
            lambda error: end(False) if error == QProcess.ProcessError.FailedToStart else None
        )
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(process.kill)
        timeout.start(600_000)
        process.start(executable, ["login"])

    def open_browser(self):
        if self.url is None or not QDesktopServices.openUrl(self.url):
            raise ValueError("尚未获得可打开的登录链接，请稍后再试。")

    def stop(self):
        process = self.process
        self.process, self.url = None, None
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(700)
