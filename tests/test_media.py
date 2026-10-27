from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PySide6.QtWidgets import QApplication

from texteditor.binary import is_video, is_audio
from texteditor.media import video_neighbor, audio_neighbor, media_neighbor

app = None


def qapp() -> QApplication:
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


try:
    from texteditor.video_view import VideoPane
    from texteditor.audio_view import AudioPane
    from texteditor.binary_stub import BinaryPane
    from texteditor.panes import make_pane
    HAS_MEDIA = True
except ImportError:  # QtMultimedia not available on this machine
    HAS_MEDIA = False


class TestKindChecks:
    def test_is_video(self):
        assert is_video("a.mp4")
        assert is_video("/x/y/B.MKV")
        assert not is_video("a.mp3")
        assert not is_video("a.png")

    def test_is_audio(self):
        assert is_audio("a.mp3")
        assert is_audio("/x/y/B.FLAC")
        assert not is_audio("a.mp4")
        assert not is_audio("a.txt")


class TestNeighbors:
    def test_video_neighbor_steps_and_wraps(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"v")
        (tmp_path / "b.mp4").write_bytes(b"v")
        (tmp_path / "c.mp3").write_bytes(b"a")
        a = str(tmp_path / "a.mp4")
        b = str(tmp_path / "b.mp4")
        assert os.path.basename(video_neighbor(a, 1)) == "b.mp4"
        assert os.path.basename(video_neighbor(a, -1)) == "b.mp4"  # wraps
        assert os.path.basename(video_neighbor(b, 1)) == "a.mp4"  # wraps
        assert os.path.basename(video_neighbor(b, -1)) == "a.mp4"

    def test_audio_neighbor_steps_and_wraps(self, tmp_path):
        (tmp_path / "x.mp3").write_bytes(b"a")
        (tmp_path / "y.mp3").write_bytes(b"a")
        x = str(tmp_path / "x.mp3")
        y = str(tmp_path / "y.mp3")
        assert os.path.basename(audio_neighbor(x, 1)) == "y.mp3"
        assert os.path.basename(audio_neighbor(y, 1)) == "x.mp3"  # wraps

    def test_media_neighbor_dispatches_by_kind(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"v")
        (tmp_path / "b.mp4").write_bytes(b"v")
        (tmp_path / "a.mp3").write_bytes(b"a")
        (tmp_path / "b.mp3").write_bytes(b"a")
        (tmp_path / "z.png").write_bytes(b"i")
        assert os.path.basename(media_neighbor(str(tmp_path / "a.mp4"), 1)) == "b.mp4"
        assert os.path.basename(media_neighbor(str(tmp_path / "a.mp3"), 1)) == "b.mp3"
        # images are not part of media navigation here
        assert media_neighbor(str(tmp_path / "z.png"), 1) is None

    def test_missing_folder(self, tmp_path):
        assert video_neighbor(str(tmp_path / "nope" / "a.mp4"), 1) is None

    def test_file_not_in_folder(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"v")
        assert video_neighbor("/elsewhere/zzz.mp4", 1) is None


@pytest.mark.skipif(not HAS_MEDIA, reason="QtMultimedia unavailable")
class TestMediaPanes:
    def test_video_pane_interface_parity(self, tmp_path):
        qapp()
        p = tmp_path / "x.mp4"
        p.write_bytes(b"fake video")
        pane = VideoPane(str(p))
        assert pane.title == "x.mp4"
        assert pane.kind == "video"
        assert pane.filepath == os.path.abspath(str(p))
        assert pane.isModified() is False
        assert pane.save() is False
        assert pane.saveAs("/tmp/whatever") is False

    def test_video_pane_set_path(self, tmp_path):
        qapp()
        a = tmp_path / "a.mp4"
        a.write_bytes(b"v")
        b = tmp_path / "b.mp4"
        b.write_bytes(b"v")
        pane = VideoPane(str(a))
        changed = []
        pane.pathChanged.connect(lambda: changed.append(1))
        pane.set_path(str(b))
        assert pane.filepath == os.path.abspath(str(b))
        assert pane.title == "b.mp4"
        assert changed

    def test_audio_pane_interface_parity(self, tmp_path):
        qapp()
        p = tmp_path / "t.mp3"
        p.write_bytes(b"fake audio")
        pane = AudioPane(str(p))
        assert pane.title == "t.mp3"
        assert pane.kind == "audio"
        assert pane.isModified() is False
        assert pane.save() is False

    def test_key_navigation_emits_steps(self, tmp_path):
        qapp()
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtGui import QKeyEvent

        p = tmp_path / "x.mp4"
        p.write_bytes(b"v")
        pane = VideoPane(str(p))
        got = []
        pane.navigateRequested.connect(got.append)
        pane.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
        pane.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
        assert got == [-1, 1]

    def test_event_filter_intercepts_child_keys(self, tmp_path):
        qapp()
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtGui import QKeyEvent

        p = tmp_path / "x.mp4"
        p.write_bytes(b"v")
        pane = VideoPane(str(p))
        got = []
        pane.navigateRequested.connect(got.append)
        # a key press aimed at the seek slider must still navigate
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
        assert pane.eventFilter(pane._seek, ev) is True
        assert got == [1]

    def test_make_pane_routes_video(self, tmp_path):
        qapp()
        p = tmp_path / "x.mp4"
        p.write_bytes(b"v")
        pane = make_pane(str(p), None)
        assert isinstance(pane, VideoPane)
        assert not isinstance(pane, BinaryPane)

    def test_make_pane_routes_audio(self, tmp_path):
        qapp()
        p = tmp_path / "x.mp3"
        p.write_bytes(b"a")
        pane = make_pane(str(p), None)
        assert isinstance(pane, AudioPane)
        assert not isinstance(pane, BinaryPane)

    def test_workspace_navigates_video_pane(self, tmp_path):
        qapp()
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtGui import QKeyEvent
        from texteditor.workspace import WorkspaceView
        from texteditor.theme import Theme

        a = tmp_path / "a.mp4"
        a.write_bytes(b"v")
        b = tmp_path / "b.mp4"
        b.write_bytes(b"v")
        view = WorkspaceView(None, Theme())
        view.set_root(str(tmp_path))
        pane = view.open_file(str(a))
        assert isinstance(pane, VideoPane)
        assert not isinstance(pane, BinaryPane)
        pane.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
        assert pane.filepath == os.path.abspath(str(b))

    def test_workspace_navigates_audio_pane(self, tmp_path):
        qapp()
        from texteditor.workspace import WorkspaceView
        from texteditor.theme import Theme

        a = tmp_path / "a.mp3"
        a.write_bytes(b"a")
        b = tmp_path / "b.mp3"
        b.write_bytes(b"a")
        view = WorkspaceView(None, Theme())
        view.set_root(str(tmp_path))
        pane = view.open_file(str(a))
        assert isinstance(pane, AudioPane)
        view._navigate_media(pane, 1)
        assert pane.filepath == os.path.abspath(str(b))
