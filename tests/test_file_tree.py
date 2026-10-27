from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QAbstractItemView

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


class TestSelectPathExpands:
    """selectPath must expand collapsed ancestor folders and select the file."""

    def test_expands_collapsed_ancestors_and_selects(self, tmp_path):
        ft = make_tree()
        root = os.fspath(tmp_path)
        sub = os.path.join(root, "a", "b")
        os.makedirs(sub)
        f = os.path.join(sub, "file.txt")
        with open(f, "w") as fh:
            fh.write("x")

        ft.setRoot(root)
        a_src = ft._model.index(os.path.join(root, "a"))
        b_src = ft._model.index(sub)
        a_idx = ft._proxy.mapFromSource(a_src)
        b_idx = ft._proxy.mapFromSource(b_src)
        ft._view.collapse(a_idx)
        ft._view.collapse(b_idx)
        assert not ft._view.isExpanded(a_idx)
        assert not ft._view.isExpanded(b_idx)

        ft.selectPath(f)

        app = QApplication.instance()
        import time
        deadline = time.time() + 3.0
        while not (ft._view.isExpanded(a_idx) and ft._view.isExpanded(b_idx)):
            app.processEvents()
            if time.time() > deadline:
                break
        assert ft._view.isExpanded(a_idx)
        assert ft._view.isExpanded(b_idx)
        cur = ft._view.currentIndex()
        assert cur.isValid()
        cur_src = ft._proxy.mapToSource(cur)
        assert os.path.normcase(os.path.normpath(ft._model.filePath(cur_src))) == \
            os.path.normcase(os.path.normpath(f))

    def test_select_path_of_missing_file_is_noop(self, tmp_path):
        ft = make_tree()
        root = os.fspath(tmp_path)
        ft.setRoot(root)
        before = ft._view.currentIndex()
        ft.selectPath(os.path.join(root, "nope.txt"))
        assert ft._view.currentIndex() == before


class TestNewWorkspaceHere:
    def test_emits_signal_for_folder(self, tmp_path):
        ft = make_tree()
        emitted = []
        ft.createWorkspaceRequested.connect(emitted.append)
        d = os.fspath(tmp_path)
        assert ft.create_workspace_here(d) is True
        assert emitted == [d]

    def test_rejects_non_folder(self, tmp_path):
        ft = make_tree()
        f = os.fspath(tmp_path) + os.sep + "a.txt"
        with open(f, "w") as fh:
            fh.write("x")
        emitted = []
        ft.createWorkspaceRequested.connect(emitted.append)
        assert ft.create_workspace_here(f) is False
        assert emitted == []

    def test_rejects_missing_path(self):
        ft = make_tree()
        assert ft.create_workspace_here("/no/such/dir/xyz") is False

    def test_main_window_creates_workspace_from_tree(self, tmp_path):
        qapp()
        from texteditor.main_window import MainWindow
        from texteditor.theme import Theme
        from texteditor.store import WorkspacesStore

        store = WorkspacesStore(os.fspath(tmp_path) + os.sep + "ws.json")
        mw = MainWindow(Theme(), store)
        try:
            d = os.fspath(tmp_path) + os.sep + "root"
            os.makedirs(d)
            before = len(mw._workspaces)
            assert mw._view.tree.create_workspace_here(d) is True
            assert len(mw._workspaces) == before + 1
            created = mw._workspaces[-1]
            assert os.path.abspath(created.root) == os.path.abspath(d)
            assert created.encrypted is False
            assert mw._current_index == len(mw._workspaces) - 1
        finally:
            mw.close()


class TestFileClipboard:
    """Copy / cut / paste of files and folders (the internal file clipboard)."""

    def _make(self, tmp_path):
        ft = make_tree()
        root = os.fspath(tmp_path)
        ft.setRoot(root)
        return ft, root

    def test_copy_file(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest_dir = os.path.join(root, "sub")
        os.makedirs(dest_dir)
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = False
        assert ft._paste_one(src, dest_dir) is True
        assert os.path.isfile(src)  # original kept (copy)
        copied = os.path.join(dest_dir, "a.txt")
        assert os.path.isfile(copied)
        with open(copied, encoding="utf-8") as fh:
            assert fh.read() == "hello"

    def test_copy_dir(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(os.path.join(src, "nested"))
        with open(os.path.join(src, "f.txt"), "w") as fh:
            fh.write("x")
        with open(os.path.join(src, "nested", "g.txt"), "w") as fh:
            fh.write("y")
        dest_dir = os.path.join(root, "dest")
        os.makedirs(dest_dir)
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = False
        assert ft._paste_one(src, dest_dir) is True
        # original intact
        assert os.path.isdir(src)
        assert os.path.isfile(os.path.join(src, "f.txt"))
        # copy created at dest_dir/folder
        new = os.path.join(dest_dir, "folder")
        assert os.path.isdir(new)
        assert os.path.isfile(os.path.join(new, "f.txt"))
        assert os.path.isfile(os.path.join(new, "nested", "g.txt"))

    def test_move_file(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest_dir = os.path.join(root, "sub")
        os.makedirs(dest_dir)
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = True
        assert ft._paste_one(src, dest_dir) is True
        assert not os.path.exists(src)  # original gone (move)
        assert os.path.isfile(os.path.join(dest_dir, "a.txt"))

    def test_move_dir(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as fh:
            fh.write("x")
        dest_dir = os.path.join(root, "other")
        os.makedirs(dest_dir)
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = True
        assert ft._paste_one(src, dest_dir) is True
        assert not os.path.exists(src)  # original gone (move)
        new = os.path.join(dest_dir, "folder")
        assert os.path.isdir(new)
        assert os.path.isfile(os.path.join(new, "f.txt"))

    def test_paste_collision_renames(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("1")
        dest_dir = os.path.join(root, "sub")
        os.makedirs(dest_dir)
        with open(os.path.join(dest_dir, "a.txt"), "w") as fh:
            fh.write("2")
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = False
        assert ft._paste_one(src, dest_dir) is True
        assert os.path.isfile(src)  # original kept
        assert os.path.isfile(os.path.join(dest_dir, "a.txt"))  # pre-existing kept
        assert os.path.isfile(os.path.join(dest_dir, "a (1).txt"))  # pasted as a renamed copy

    def test_paste_dir_into_itself_rejected(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(os.path.join(src, "inner"))
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = False
        assert ft._paste_one(src, src) is False  # into itself
        assert ft._paste_one(src, os.path.join(src, "inner")) is False  # into a subfolder

    def test_paste_missing_source_rejected(self, tmp_path):
        ft, root = self._make(tmp_path)
        dest_dir = os.path.join(root, "dest")
        os.makedirs(dest_dir)
        ft._file_clipboard = [os.path.join(root, "nope.txt")]
        ft._file_clipboard_cut = False
        assert ft._paste_one(os.path.join(root, "nope.txt"), dest_dir) is False

    def test_unique_dest_skips_taken_numbers(self, tmp_path):
        ft, root = self._make(tmp_path)
        d = os.path.join(root, "d")
        os.makedirs(d)
        with open(os.path.join(d, "x.txt"), "w") as fh:
            fh.write("1")
        with open(os.path.join(d, "x (1).txt"), "w") as fh:
            fh.write("2")
        # x.txt and x (1).txt are taken -> next free is x (2).txt
        assert ft._unique_dest(os.path.join(d, "x.txt")) == os.path.join(d, "x (2).txt")

    def test_context_menu_contains_clipboard_actions(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        ft.selectPath(src)

        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        assert menu is not None
        acts = ctx["acts"]
        assert "copy" in acts and "cut" in acts and "paste" in acts
        # Paste disabled: internal clipboard empty. Copy/Cut enabled: an item selected.
        assert acts["paste"].isEnabled() is False
        assert acts["copy"].isEnabled() is True
        assert acts["cut"].isEnabled() is True
        # Menu text is present (for the user) and unique.
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert texts.count("Copy") == 1

    def test_context_menu_copy_and_paste_dispatch(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest_dir = os.path.join(root, "sub")
        os.makedirs(dest_dir)
        ft.selectPath(src)

        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        # Copy dispatches to the internal clipboard.
        ft._dispatch_context(ctx["acts"]["copy"], ctx)
        assert ft._file_clipboard == [os.path.abspath(src)]
        assert ft._file_clipboard_cut is False
        # Paste dispatches into the selected folder (here: the root, since a file is selected).
        ft._dispatch_context(ctx["acts"]["paste"], ctx)
        assert os.path.isfile(os.path.join(root, "a (1).txt"))
        assert os.path.isfile(src)  # original kept (copy, not move)

    def test_context_menu_cut_then_paste_moves(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest_dir = os.path.join(root, "sub")
        os.makedirs(dest_dir)
        ft.selectPath(src)

        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        ft._dispatch_context(ctx["acts"]["cut"], ctx)
        assert ft._file_clipboard_cut is True
        # Select the destination folder, then paste -> the file moves into it.
        ft.selectPath(dest_dir)
        menu, ctx2 = ft._build_context_menu(ft._view.viewport().rect().center())
        ft._dispatch_context(ctx2["acts"]["paste"], ctx2)
        assert os.path.isfile(os.path.join(dest_dir, "a.txt"))
        assert not os.path.exists(src)  # original removed (cut)
        assert ft._file_clipboard == []  # clipboard cleared after a cut-paste

    def test_context_menu_paste_enabled_when_clipboard_has_items(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        ft._file_clipboard = [src]
        ft._file_clipboard_cut = False
        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        assert ctx["acts"]["paste"].isEnabled() is True


class TestDropFiles:
    """Drag-and-drop move/copy of files and folders (``FileTree.drop_files``)."""

    def _make(self, tmp_path):
        qapp()
        ft = FileTree()
        root = str(tmp_path)
        ft.setRoot(root)
        return ft, root

    def test_drop_move_file(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest = os.path.join(root, "sub")
        os.makedirs(dest)
        assert ft.drop_files([src], dest, copy=False) is True
        assert os.path.isfile(os.path.join(dest, "a.txt"))
        assert not os.path.exists(src)  # moved, original gone

    def test_drop_copy_file(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("hello")
        dest = os.path.join(root, "sub")
        os.makedirs(dest)
        assert ft.drop_files([src], dest, copy=True) is True
        assert os.path.isfile(os.path.join(dest, "a.txt"))
        assert os.path.isfile(src)  # original kept (copy)

    def test_drop_move_folder(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(os.path.join(src, "inner"))
        with open(os.path.join(src, "inner", "x.txt"), "w") as fh:
            fh.write("x")
        dest = os.path.join(root, "dest")
        os.makedirs(dest)
        assert ft.drop_files([src], dest, copy=False) is True
        assert os.path.isfile(os.path.join(dest, "folder", "inner", "x.txt"))
        assert not os.path.exists(src)

    def test_drop_copy_folder(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(os.path.join(src, "inner"))
        with open(os.path.join(src, "inner", "x.txt"), "w") as fh:
            fh.write("x")
        dest = os.path.join(root, "dest")
        os.makedirs(dest)
        assert ft.drop_files([src], dest, copy=True) is True
        assert os.path.isfile(os.path.join(dest, "folder", "inner", "x.txt"))
        assert os.path.isfile(os.path.join(src, "inner", "x.txt"))  # original kept

    def test_drop_collision_renames(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "a.txt")
        with open(src, "w") as fh:
            fh.write("src")
        dest = os.path.join(root, "dest")
        os.makedirs(dest)
        with open(os.path.join(dest, "a.txt"), "w") as fh:
            fh.write("existing")
        assert ft.drop_files([src], dest, copy=True) is True
        assert os.path.isfile(os.path.join(dest, "a (1).txt"))
        assert os.path.isfile(src)

    def test_drop_into_own_subfolder_rejected(self, tmp_path):
        ft, root = self._make(tmp_path)
        src = os.path.join(root, "folder")
        os.makedirs(os.path.join(src, "inner"))
        assert ft.drop_files([src], os.path.join(src, "inner"), copy=False) is False
        assert os.path.isdir(src)  # unchanged

    def test_drop_view_is_dnd_enabled(self, tmp_path):
        from PySide6.QtCore import Qt
        from texteditor.file_tree import _FileExplorerView
        ft, root = self._make(tmp_path)
        assert isinstance(ft._view, _FileExplorerView)
        assert ft._view.dragDropMode() == QAbstractItemView.DragDrop
        assert ft._view.dragEnabled() is True
        assert ft._view.acceptDrops() is True
        assert ft._view.defaultDropAction() == Qt.MoveAction
