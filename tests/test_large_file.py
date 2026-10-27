from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

from texteditor import large_file
from texteditor.large_file import (
    MAX_SIZE,
    WARN_SIZE,
    confirm_open,
    file_size,
    human_size,
)
from texteditor.theme import Theme
from texteditor.workspace import WorkspaceView

app = None


def qapp():
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_view() -> WorkspaceView:
    qapp()
    return WorkspaceView(None, Theme())


def write_text(dirpath: str, name: str) -> str:
    """A small, pure-text file (no null bytes) so it classifies as text."""
    p = os.path.join(dirpath, name)
    with open(p, "w") as fh:
        fh.write("hello line one\n" * 1000)
    return p


def write_binary(dirpath: str, name: str) -> str:
    p = os.path.join(dirpath, name)
    with open(p, "wb") as fh:
        fh.write(b"\x00\x01\x02\x03")
    return p


def fake_size(monkeypatch, size: int) -> None:
    """Make large_file.file_size report a fixed size regardless of the real file."""
    monkeypatch.setattr(large_file, "file_size", lambda path, fs=None: size)


class _FakeBox:
    """Stands in for QMessageBox: records calls, returns a fixed answer."""

    Yes = QMessageBox.Yes
    No = QMessageBox.No

    def __init__(self, calls: list, answer=QMessageBox.No) -> None:
        self.calls = calls
        self.answer = answer

    def warning(self, *a, **k):
        self.calls.append("warning")

    def question(self, *a, **k):
        self.calls.append("question")
        return self.answer


class TestFileSize:
    def test_real_file(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("12345")
        assert file_size(str(p)) == 5

    def test_missing_file(self, tmp_path):
        assert file_size(str(tmp_path / "nope.txt")) is None

    def test_with_fs(self, tmp_path):
        class FakeFs:
            def size(self, path):
                return 12345

        class BrokenFs:
            def size(self, path):
                raise OSError("boom")

        p = tmp_path / "a.txt"
        p.write_text("x")
        assert file_size(str(p), fs=FakeFs()) == 12345
        assert file_size(str(p), fs=BrokenFs()) is None


class TestHumanSize:
    def test_bytes(self):
        assert human_size(512) == "512 B"

    def test_kb(self):
        assert human_size(2048) == "2.0 KB"

    def test_mb(self):
        assert human_size(10 * 1024 * 1024) == "10.0 MB"

    def test_gb(self):
        assert human_size(int(1.5 * 1024 ** 3)) == "1.5 GB"


class TestConfirmOpen:
    def test_small_file_opens_without_dialog(self, tmp_path, monkeypatch):
        p = write_text(os.fspath(tmp_path), "small.txt")
        calls: list = []
        fake_size(monkeypatch, 1024)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        assert confirm_open(p) is True
        assert calls == []

    def test_huge_file_refused(self, tmp_path, monkeypatch):
        p = write_text(os.fspath(tmp_path), "huge.txt")
        calls: list = []
        fake_size(monkeypatch, MAX_SIZE + 1)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        assert confirm_open(p) is False
        assert "warning" in calls

    def test_mid_file_user_says_yes(self, tmp_path, monkeypatch):
        p = write_text(os.fspath(tmp_path), "mid.txt")
        calls: list = []
        fake_size(monkeypatch, WARN_SIZE + 1)
        monkeypatch.setattr(
            large_file, "QMessageBox", _FakeBox(calls, answer=QMessageBox.Yes)
        )
        assert confirm_open(p) is True
        assert "question" in calls

    def test_mid_file_user_says_no(self, tmp_path, monkeypatch):
        p = write_text(os.fspath(tmp_path), "mid.txt")
        calls: list = []
        fake_size(monkeypatch, WARN_SIZE + 1)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        assert confirm_open(p) is False
        assert "question" in calls

    def test_unknown_size_opens(self, tmp_path, monkeypatch):
        # If the size can't be determined, fail open (don't block the user).
        p = write_text(os.fspath(tmp_path), "unknown.txt")
        calls: list = []
        monkeypatch.setattr(large_file, "file_size", lambda path, fs=None: None)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        assert confirm_open(p) is True
        assert calls == []


class TestOpenFileGuard:
    def test_huge_text_file_is_not_opened(self, tmp_path, monkeypatch):
        view = make_view()
        before = len(view.panes())
        p = write_text(os.fspath(tmp_path), "huge.txt")
        fake_size(monkeypatch, MAX_SIZE + 1)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox([]))
        pane = view.open_file(p)
        assert pane is None
        assert len(view.panes()) == before

    def test_mid_text_file_declined(self, tmp_path, monkeypatch):
        view = make_view()
        before = len(view.panes())
        p = write_text(os.fspath(tmp_path), "mid.txt")
        fake_size(monkeypatch, WARN_SIZE + 1)
        monkeypatch.setattr(
            large_file, "QMessageBox", _FakeBox([], answer=QMessageBox.No)
        )
        pane = view.open_file(p)
        assert pane is None
        assert len(view.panes()) == before

    def test_mid_text_file_accepted(self, tmp_path, monkeypatch):
        view = make_view()
        p = write_text(os.fspath(tmp_path), "mid.txt")
        fake_size(monkeypatch, WARN_SIZE + 1)
        monkeypatch.setattr(
            large_file, "QMessageBox", _FakeBox([], answer=QMessageBox.Yes)
        )
        pane = view.open_file(p)
        assert pane is not None
        assert view.pane_for_path(p) is pane

    def test_small_file_opens_without_dialog(self, tmp_path, monkeypatch):
        view = make_view()
        p = write_text(os.fspath(tmp_path), "small.txt")
        calls: list = []
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        pane = view.open_file(p)
        assert pane is not None
        assert calls == []

    def test_huge_binary_file_not_gated(self, tmp_path, monkeypatch):
        # Binaries don't load fully into memory, so the guard must not block them
        # even when the reported size is enormous.
        view = make_view()
        p = write_binary(os.fspath(tmp_path), "big.bin")
        fake_size(monkeypatch, MAX_SIZE + 1)
        calls: list = []
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        pane = view.open_file(p)
        assert pane is not None
        assert calls == []

    def test_reopening_an_open_file_is_not_gated(self, tmp_path, monkeypatch):
        view = make_view()
        p = write_text(os.fspath(tmp_path), "small.txt")
        pane = view.open_file(p)
        assert pane is not None

        calls: list = []
        fake_size(monkeypatch, MAX_SIZE + 1)
        monkeypatch.setattr(large_file, "QMessageBox", _FakeBox(calls))
        again = view.open_file(p)
        assert again is pane
        assert calls == []
