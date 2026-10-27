"""Bottom status bar: cursor position, word count, encoding."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from .theme import Theme


class StatusBar(QWidget):
    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet(
            f"background: #1e1e1e; border-top: 1px solid #2d2d2d; color: #9d9d9d; font-size: 12px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(16)

        self._pos_label = QLabel("", self)
        self._word_label = QLabel("", self)
        self._enc_label = QLabel("", self)
        self._path_label = QLabel("", self)
        self._path_label.setAlignment(Qt.AlignRight)

        layout.addWidget(self._pos_label)
        layout.addWidget(self._word_label)
        layout.addWidget(self._enc_label)
        layout.addStretch()
        layout.addWidget(self._path_label)

    def update_from_pane(self, pane) -> None:
        if pane is None or not hasattr(pane, "textCursor"):
            self._pos_label.setText("")
            self._word_label.setText("")
            self._enc_label.setText("")
            self._path_label.setText("")
            return
        cursor = pane.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self._pos_label.setText(f"Ln {line}, Col {col}")

        text = pane.toPlainText()
        words = len(text.split())
        lines = len(text.splitlines())
        self._word_label.setText(f"{words} words, {lines} lines")

        enc = getattr(pane, "encoding", None)
        self._enc_label.setText(enc or "utf-8")

        path = getattr(pane, "filepath", None)
        self._path_label.setText(path or "")
