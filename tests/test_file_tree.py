from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from texteditor.file_tree import FileTree
from texteditor.todos import BUCKETS, todos_from_dict

app = None


def qapp():
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_tree() -> FileTree:
    qapp()
    return FileTree()


class TestNewTodo:
    def test_creates_valid_todos_file(self, tmp_path):
        ft = make_tree()
        p = ft.new_todo_in(os.fspath(tmp_path), "mylist")
        assert p is not None and os.path.isfile(p)
        assert p == os.path.join(os.fspath(tmp_path), "mylist.todo")
        data = json.load(open(p, encoding="utf-8"))
        assert data["version"] == 1
        t = todos_from_dict(data)
        cats = t.categories()
        assert len(cats) == 1
        for b in BUCKETS:
            assert t.tasks(cats[0], b) == []

    def test_appends_todo_suffix(self, tmp_path):
        ft = make_tree()
        p = ft.new_todo_in(os.fspath(tmp_path), "work")
        assert p is not None and p.endswith(".todo")

    def test_keeps_existing_todo_suffix(self, tmp_path):
        ft = make_tree()
        p = ft.new_todo_in(os.fspath(tmp_path), "already.todo")
        assert p is not None and p.endswith("already.todo") and not p.endswith("todo.todo")

    def test_rejects_existing_file(self, tmp_path):
        ft = make_tree()
        d = os.fspath(tmp_path)
        assert ft.new_todo_in(d, "x") is not None
        assert ft.new_todo_in(d, "x") is None

    def test_blank_name_rejected(self, tmp_path):
        ft = make_tree()
        assert ft.new_todo_in(os.fspath(tmp_path), "   ") is None


class TestTodoRouting:
    """``.todo`` files must open in a Todos tab, not the text editor."""

    def test_open_file_routes_todo(self, tmp_path):
        qapp()
        from texteditor.workspace import WorkspaceView
        from texteditor.todos_pane import TodosPane
        from texteditor.theme import Theme

        view = WorkspaceView(None, Theme())
        todo = os.fspath(tmp_path) + os.sep + "x.todo"
        ft = FileTree()
        assert ft.new_todo_in(os.fspath(tmp_path), "x") == todo

        pane = view.open_file(todo)
        assert isinstance(pane, TodosPane)
        assert pane.filepath == todo
        assert "General" in pane.todos().categories()
        assert view.tab_count() == 1

    def test_open_file_ignores_other_extensions(self, tmp_path):
        qapp()
        from texteditor.workspace import WorkspaceView
        from texteditor.todos_pane import TodosPane
        from texteditor.theme import Theme

        view = WorkspaceView(None, Theme())
        txt = os.fspath(tmp_path) + os.sep + "x.todo.txt"
        with open(txt, "w") as fh:
            fh.write("hi")
        pane = view.open_file(txt)
        assert pane is not None
        assert not isinstance(pane, TodosPane)  # a text pane was created
        assert view.tab_count() == 1

    def test_main_window_open_todo_file(self, tmp_path):
        qapp()
        from texteditor.main_window import MainWindow
        from texteditor.todos_pane import TodosPane
        from texteditor.theme import Theme
        from texteditor.store import WorkspacesStore

        store = WorkspacesStore(os.fspath(tmp_path) + os.sep + "ws.json")
        mw = MainWindow(Theme(), store)
        try:
            todo = os.fspath(tmp_path) + os.sep + "main.todo"
            ft = FileTree()
            assert ft.new_todo_in(os.fspath(tmp_path), "main") is not None

            mw.open_paths([todo])
            panes = [p for p in mw._view.panes() if isinstance(p, TodosPane)]
            assert len(panes) == 1
            assert panes[0].filepath == todo
            assert "General" in panes[0].todos().categories()
        finally:
            mw.close()


class TestRevealInExplorer:
    def test_reveal_selected_emits_signal(self, tmp_path):
        ft = make_tree()
        f = os.fspath(tmp_path) + os.sep + "a.txt"
        with open(f, "w") as fh:
            fh.write("x")
        emitted = []
        ft.revealInExplorerRequested.connect(emitted.append)
        assert ft.reveal_selected([f]) == [f]
        assert emitted == [f]

    def test_reveal_selected_ignores_missing(self, tmp_path):
        ft = make_tree()
        emitted = []
        ft.revealInExplorerRequested.connect(emitted.append)
        missing = os.fspath(tmp_path) + os.sep + "nope.txt"
        assert ft.reveal_selected([missing]) == []
        assert emitted == []
