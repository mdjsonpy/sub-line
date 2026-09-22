"""Application bootstrap: QApplication, theme, main window."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from . import APP_DISPLAY_NAME
from .theme import Theme
from .store import WorkspacesStore
from .main_window import MainWindow

_PATH_FLAGS = {"--help", "-h", "--version", "-v"}


def _requested_paths() -> list[str]:
    """Collect file/folder arguments from the command line."""
    return [a for a in sys.argv[1:] if a not in _PATH_FLAGS and not a.startswith("-")]


class TextEditorApp:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_DISPLAY_NAME)
        self.theme = Theme()
        self.app.setStyleSheet(self.theme.stylesheet())
        self.store = WorkspacesStore()
        self.main = MainWindow(self.theme, self.store)
        self._paths = [p for p in _requested_paths() if os.path.exists(os.path.expanduser(p))]

    def run(self) -> int:
        if self._paths:
            QTimer.singleShot(0, lambda: self.main.open_paths(self._paths))
        self.main.show()
        return self.app.exec()


def main() -> int:
    app = TextEditorApp()
    return app.run()
