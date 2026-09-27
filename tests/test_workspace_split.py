from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from texteditor.workspace import WorkspaceView
from texteditor.theme import Theme
from texteditor.editor_view import EditorPane

app = None


def qapp():
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_view() -> WorkspaceView:
    qapp()
    return WorkspaceView(None, Theme())


def setup_split(view: WorkspaceView) -> tuple:
    """Create a split with one untitled pane in each group. Returns (left, right)."""
    view._ensure_right_group()
    left = view._groups[0]
    right = view._groups[-1]
    view._add_pane_to(left, EditorPane(view._theme))
    view._add_pane_to(right, EditorPane(view._theme))
    return left, right


def write_file(dirpath: str, name: str) -> str:
    p = os.path.join(dirpath, name)
    with open(p, "w") as fh:
        fh.write("x")
    return p


class TestFocusTargetGroup:
    def test_focus_switches_target_group(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)

        # User is currently working in the LEFT group
        view._active_group = left
        assert view.active_group() is left

        # User clicks into the RIGHT group -> focus moves there
        view._on_focus_changed(None, right.currentWidget())
        assert view._active_group is right
        assert view.active_group() is right

    def test_focus_back_to_left(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)

        view._active_group = right
        assert view.active_group() is right

        view._on_focus_changed(None, left.currentWidget())
        assert view._active_group is left
        assert view.active_group() is left

    def test_group_of_widget_resolves_child(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)

        pane = right.currentWidget()
        # The pane is a QPlainTextEdit; its viewport is the actual focus target.
        child = pane.viewport() if hasattr(pane, "viewport") else pane
        assert view._group_of_widget(child) is right
        assert view._group_of_widget(pane) is right

    def test_group_of_widget_none_for_outsider(self, tmp_path):
        view = make_view()
        # The tree is not part of any tab group
        assert view._group_of_widget(view.tree) is None


class TestOpenFileTarget:
    def test_open_file_targets_focused_group(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)

        # Start with LEFT active, then focus RIGHT
        view._active_group = left
        view._on_focus_changed(None, right.currentWidget())
        assert view.active_group() is right

        f = write_file(os.fspath(tmp_path), "new.txt")
        pane = view.open_file(f)
        assert view._group_of(pane) is right

    def test_open_file_targets_left_when_left_focused(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)

        # Start with RIGHT active, then focus LEFT
        view._active_group = right
        view._on_focus_changed(None, left.currentWidget())
        assert view.active_group() is left

        f = write_file(os.fspath(tmp_path), "new.txt")
        pane = view.open_file(f)
        assert view._group_of(pane) is left

    def test_focus_changed_signal_updates_active_group(self, tmp_path):
        view = make_view()
        left, right = setup_split(view)
        view.show()  # offscreen focus only lands on a shown widget

        view._active_group = left
        target = right.currentWidget()
        target.setFocus()
        a = qapp()
        deadline = time.time() + 2.0
        while view._active_group is not right and time.time() < deadline:
            a.processEvents()
        assert view._active_group is right
