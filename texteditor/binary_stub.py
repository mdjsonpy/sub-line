"""Placeholder pane shown for image / video / audio / unknown binary files.

Media rendering is a planned future feature; for now this gives a clear,
styled "coming soon" view so binary files don't corrupt the text editor.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QPen, QBrush
from PySide6.QtCore import QRectF

from .binary import (
    KIND_IMAGE,
    KIND_VIDEO,
    KIND_AUDIO,
    KIND_BINARY,
)

_KIND_META = {
    KIND_IMAGE: ("IMG", "#4ec9b0"),
    KIND_VIDEO: ("VID", "#c586c0"),
    KIND_AUDIO: ("AUD", "#ce9178"),
    KIND_BINARY: ("BIN", "#969696"),
}


def _make_badge(text: str, color_hex: str, size: int = 96) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(color_hex)
    p.setBrush(QBrush(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 18, 18)
    p.setPen(QPen(QColor("#1e1e1e"), 3))
    font = QFont()
    font.setPointSize(int(size / 5))
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#1e1e1e"))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
    p.end()
    return pm


def _human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num} TB"


class BinaryPane(QWidget):
    """Read-only placeholder for non-text files."""

    modificationChanged = Signal(bool)

    def __init__(self, path: str, kind: str, parent=None, fs=None) -> None:
        super().__init__(parent)
        self.filepath: str | None = path
        self.kind = kind
        self._fs = fs

        meta = _KIND_META.get(kind, _KIND_META[KIND_BINARY])
        label, color = meta

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        badge = QLabel(self)
        badge.setPixmap(_make_badge(label, color))
        badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(badge, alignment=Qt.AlignHCenter)

        name = QLabel(os.path.basename(path) or path, self)
        name.setAlignment(Qt.AlignCenter)
        f = name.font()
        f.setPointSize(13)
        f.setBold(True)
        name.setFont(f)
        layout.addWidget(name)

        size_label = QLabel(self)
        size_label.setAlignment(Qt.AlignCenter)
        try:
            if fs is not None:
                size_label.setText(_human_size(fs.size(path)))
            else:
                st = os.stat(path)
                size_label.setText(_human_size(st.st_size))
        except (OSError, FileNotFoundError):
            size_label.setText("")
        layout.addWidget(size_label)

        kind_name = {
            KIND_IMAGE: "image",
            KIND_VIDEO: "video",
            KIND_AUDIO: "audio",
            KIND_BINARY: "binary",
        }.get(kind, "file")

        note = QLabel(
            f"This is a {kind_name} file.\nPreview support is coming in a future version.",
            self,
        )
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(note)

    # --- interface parity with EditorPane -------------------------------------
    @property
    def title(self) -> str:
        return os.path.basename(self.filepath) if self.filepath else "Untitled"

    def isModified(self) -> bool:  # noqa: N802
        return False

    def save(self) -> bool:
        return False

    def saveAs(self, path: str) -> bool:  # noqa: N802
        return False
