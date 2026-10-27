"""Video playback pane: video surface + shared transport controls.

Read-only — mirrors the EditorPane / ImagePane interface (title, isModified,
save, saveAs) so tab management works unchanged. Left/Right arrows navigate
to the previous/next video in the same folder.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QWidget
from PySide6.QtMultimediaWidgets import QVideoWidget

from .binary import KIND_VIDEO
from .media import BaseMediaPane


class VideoPane(BaseMediaPane):
    """Read-only video playback pane (one tab's content)."""

    def __init__(self, path: str, parent=None, fs=None) -> None:
        super().__init__(KIND_VIDEO, parent)
        self.filepath = os.path.abspath(path)
        self._load(self.filepath)

    def _build_visual(self) -> QWidget:
        surface = QVideoWidget(self)
        self._player.setVideoOutput(surface)
        return surface
