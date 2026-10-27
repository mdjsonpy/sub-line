"""Audio playback pane: no video surface, just the shared transport bar.

Read-only — mirrors the EditorPane / ImagePane interface (title, isModified,
save, saveAs) so tab management works unchanged. Left/Right arrows navigate
to the previous/next audio file in the same folder.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt

from .binary import KIND_AUDIO
from .media import BaseMediaPane


class AudioPane(BaseMediaPane):
    """Read-only audio playback pane (one tab's content)."""

    def __init__(self, path: str, parent=None, fs=None) -> None:
        super().__init__(KIND_AUDIO, parent)
        self.filepath = os.path.abspath(path)
        self._load(self.filepath)

    def _build_visual(self) -> QLabel:
        hint = QLabel("Audio playback — use the controls below", self)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #8a8a8a;")
        return hint
