"""Shared media playback pane: transport controls + folder navigation.

Video and audio panes share one QMediaPlayer transport (play/pause, seek,
mute, volume) and arrow-key navigation between neighboring files of the
same kind in the same folder (mirroring the image pane).

Media loads paused at 0:00 — no autoplay. Mirrors the EditorPane / ImagePane
interface (title, isModified, save, saveAs) so tab management works unchanged.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QLabel,
)
from PySide6.QtCore import Qt, Signal, QUrl, QEvent
from PySide6.QtMultimedia import QMediaPlayer

from .binary import is_audio, is_video


def _fmt(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60}:{s % 60:02d}"


def _neighbor(path: str, step: int, predicate) -> str | None:
    """Previous/next file matching ``predicate`` in the same folder.

    Alphabetical (case-insensitive), wraps around. None if the folder has no
    such files or cannot be listed.
    """
    folder = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(path)
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    files = sorted(
        (e for e in entries if predicate(os.path.join(folder, e))), key=str.lower
    )
    if name not in files:
        return None
    i = files.index(name)
    return os.path.join(folder, files[(i + step) % len(files)])


def video_neighbor(path: str, step: int) -> str | None:
    """Previous/next video in the same folder (alphabetical, wraps)."""
    return _neighbor(path, step, is_video)


def audio_neighbor(path: str, step: int) -> str | None:
    """Previous/next audio file in the same folder (alphabetical, wraps)."""
    return _neighbor(path, step, is_audio)


def media_neighbor(path: str, step: int) -> str | None:
    """Previous/next video or audio file in the same folder.

    Dispatches on the file's own kind so navigation never crosses types.
    """
    if is_video(path):
        return video_neighbor(path, step)
    if is_audio(path):
        return audio_neighbor(path, step)
    return None


class BaseMediaPane(QWidget):
    """Read-only media playback pane (one tab's content).

    Subclasses override ``_build_visual()`` to provide the area above the
    transport bar (video surface, audio placeholder, ...).
    """

    modificationChanged = Signal(bool)
    pathChanged = Signal()          # emitted when filepath changes (navigation)
    navigateRequested = Signal(int) # Left = -1, Right = +1

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.filepath: str | None = None

        self._player = QMediaPlayer(self)
        ao = self._player.audioOutput()
        if ao is not None:
            ao.setVolume(1.0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_visual(), 1)
        layout.addWidget(self._build_transport_bar(), 0)

        self._seeking = False
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._seek.sliderPressed.connect(self._on_seek_pressed)
        self._seek.sliderReleased.connect(self._on_seek_released)

        # Left/Right must navigate even when a control holds focus
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    # --- subclass hook ----------------------------------------------------------
    def _build_visual(self) -> QWidget:
        """Area above the transport bar. Default: blank themed surface."""
        return QWidget(self)

    # --- transport bar -----------------------------------------------------------
    def _build_transport_bar(self) -> QWidget:
        bar = QWidget(self)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(6)

        self._play_btn = QPushButton("▶", bar)
        self._play_btn.setFixedSize(34, 26)
        self._play_btn.clicked.connect(self.toggle_play)
        h.addWidget(self._play_btn)

        self._pos_lbl = QLabel("0:00", bar)
        self._pos_lbl.setFixedWidth(42)
        h.addWidget(self._pos_lbl)

        self._seek = QSlider(Qt.Horizontal, bar)
        self._seek.setRange(0, 0)
        h.addWidget(self._seek, 1)

        self._dur_lbl = QLabel("0:00", bar)
        self._dur_lbl.setFixedWidth(42)
        h.addWidget(self._dur_lbl)

        self._mute_btn = QPushButton("Mute", bar)
        self._mute_btn.setCheckable(True)
        self._mute_btn.toggled.connect(self._on_mute)
        h.addWidget(self._mute_btn)

        self._vol = QSlider(Qt.Horizontal, bar)
        self._vol.setRange(0, 100)
        self._vol.setValue(100)
        self._vol.setFixedWidth(80)
        self._vol.valueChanged.connect(self._on_volume)
        h.addWidget(self._vol)

        self._status_lbl = QLabel(bar)
        self._status_lbl.setStyleSheet("color: #e06c75;")
        self._status_lbl.hide()
        h.addWidget(self._status_lbl)
        return bar

    # --- transport logic -----------------------------------------------------------
    def toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("❚❚" if playing else "▶")

    def _on_position(self, pos: int) -> None:
        if self._seeking:
            return
        self._seek.setValue(pos)
        self._pos_lbl.setText(_fmt(pos))

    def _on_duration(self, dur: int) -> None:
        self._seek.setRange(0, max(0, dur))
        self._dur_lbl.setText(_fmt(dur))

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._status_lbl.setText("Could not play this file")
            self._status_lbl.show()
        else:
            self._status_lbl.hide()

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        self._player.setPosition(self._seek.value())

    def _on_mute(self, on: bool) -> None:
        ao = self._player.audioOutput()
        if ao is not None:
            ao.setMuted(on)

    def _on_volume(self, v: int) -> None:
        ao = self._player.audioOutput()
        if ao is not None:
            ao.setVolume(v / 100)
            if v > 0 and self._mute_btn.isChecked():
                self._mute_btn.setChecked(False)

    # --- navigation ------------------------------------------------------------------
    def set_path(self, path: str) -> None:
        """Swap the played file in place (title updates via pathChanged)."""
        path = os.path.abspath(path)
        if path == self.filepath:
            return
        self.filepath = path
        self._load(path)
        self.pathChanged.emit()

    def _load(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.pause()
        self._player.setPosition(0)
        self._status_lbl.hide()
        self._pos_lbl.setText("0:00")
        self._dur_lbl.setText("0:00")
        self._seek.setRange(0, 0)
        self._seek.setValue(0)
        self._on_state(QMediaPlayer.PlaybackState.PausedState)

    # --- interface parity with EditorPane / ImagePane --------------------------------
    @property
    def title(self) -> str:
        return os.path.basename(self.filepath) if self.filepath else "Media"

    def isModified(self) -> bool:  # noqa: N802
        return False

    def save(self) -> bool:
        return False

    def saveAs(self, path: str) -> bool:  # noqa: N802
        return False

    # --- keyboard --------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self.navigateRequested.emit(-1)
            return
        if event.key() == Qt.Key_Right:
            self.navigateRequested.emit(1)
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Left:
                self.navigateRequested.emit(-1)
                return True
            if event.key() == Qt.Key_Right:
                self.navigateRequested.emit(1)
                return True
        return super().eventFilter(obj, event)

    # --- lifecycle ---------------------------------------------------------------------
    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._player.pause()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._player.stop()
        super().closeEvent(event)
