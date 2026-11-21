import faulthandler
import os
import sys
from contextlib import ExitStack
from pathlib import Path

if sys.platform.startswith("linux") and "--x11" in sys.argv:
    sys.argv.remove("--x11")
    os.environ.update(QT_QPA_PLATFORM="xcb", TINGSU_CAPTURE_DESKTOP="x11")
elif sys.platform.startswith("linux") and "--wayland" in sys.argv:
    sys.argv.remove("--wayland")
    os.environ.update(QT_QPA_PLATFORM="wayland", TINGSU_CAPTURE_DESKTOP="wayland")
elif os.environ.get("XDG_SESSION_TYPE") == "wayland" and os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")

from PySide6.QtCore import QLockFile, QUrl, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .bridge import Bridge
from .runtime import app_directory
from .storage import DATA, private_dir

UI = Path(__file__).resolve().parent / "ui"


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        return is_main_frame and url.isLocalFile() and Path(url.toLocalFile()) == UI / "index.html"


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("听序 · 会议工作台")
        self.setWindowIcon(QIcon(str(UI / "tingsu.svg")))
        geom = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1420, int(geom.width() * 0.92)), min(940, int(geom.height() * 0.9)))
        self.setMinimumSize(820, 530)
        self.view = QWebEngineView(self)
        page = LocalPage(self.view)
        self.view.setPage(page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("studio", self.bridge)
        page.setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str(UI / "index.html")))

    def closeEvent(self, event):
        if self.bridge.has_active_work:
            answer = QMessageBox.question(
                self,
                "退出听序",
                "退出会结束当前采集和 AI 回复。已录制但尚未转写的分片可在下次打开会议时重试。是否退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.bridge.shutdown()
        event.accept()


def main():
    os.umask(0o077)
    app = QApplication(sys.argv)
    app.setApplicationName("听序")
    app.setDesktopFileName("tingsu")
    lock = QLockFile(str(private_dir(DATA) / "studio.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "听序", "同一个本地工作空间已经打开。")
        return 1
    with ExitStack() as cleanup:
        # Native faults bypass Python exceptions. Keep the last stack locally so a report can
        # distinguish a fatal desktop error from a crashed renderer, agent or capture helper.
        try:
            path = private_dir(app_directory("cache")) / "desktop-fault.log"
            if path.exists() and path.stat().st_size > 512 * 1024:
                path.replace(path.with_suffix(".previous.log"))
            fault_log = cleanup.enter_context(path.open("ab"))
            faulthandler.enable(file=fault_log, all_threads=True)
            cleanup.callback(faulthandler.disable)
        except OSError:
            pass
        studio = Studio()
        studio.show()
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
