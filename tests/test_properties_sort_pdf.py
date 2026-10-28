"""Tests for the file-tree Properties dialog, the Sort-by menu, and the PDF pane.

Covers the three features added to the file tree:
  * right-click "Properties" showing file/folder size (real workspaces only)
  * a top "Sort" button (name / size / modified / type, asc / desc)
  * opening PDF files in a native QtPdf read-only pane (disk and encrypted)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

app = None


def qapp():
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_tree():
    qapp()
    from texteditor.file_tree import FileTree

    return FileTree()


def tree_at(tmp_path, memory=False):
    """A FileTree rooted at an existing directory (required to build the menu)."""
    ft = make_tree()
    ft.setRoot(os.fspath(tmp_path))
    if memory:
        ft._is_memory = True
    return ft


# --- Properties --------------------------------------------------------------
class TestProperties:
    def test_human_size_units(self):
        from texteditor.file_tree import FileTree

        assert FileTree.human_size(0) == "0 B"
        assert FileTree.human_size(1023) == "1023 B"
        assert FileTree.human_size(1024) == "1.0 KB"
        assert FileTree.human_size(1536) == "1.5 KB"
        assert FileTree.human_size(2048) == "2.0 KB"
        assert FileTree.human_size(1024 * 1024) == "1.0 MB"
        assert FileTree.human_size(5 * 1024 * 1024 * 1024) == "5.0 GB"

    def test_file_size_file(self, tmp_path):
        ft = make_tree()
        f = os.fspath(tmp_path) + os.sep + "a.txt"
        with open(f, "w") as fh:
            fh.write("hello")  # 5 bytes
        assert ft.file_size(f) == 5

    def test_file_size_folder_sums_contents(self, tmp_path):
        ft = make_tree()
        d = os.path.join(os.fspath(tmp_path), "dir")
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "x.txt"), "w") as fh:
            fh.write("12345")  # 5
        with open(os.path.join(d, "sub", "y.txt"), "w") as fh:
            fh.write("1")  # 1
        assert ft.file_size(d) == 6

    def test_properties_text_single_file(self, tmp_path):
        ft = make_tree()
        f = os.fspath(tmp_path) + os.sep + "a.txt"
        with open(f, "w") as fh:
            fh.write("hello")
        text = ft.properties_text([f])
        assert "a.txt" in text
        assert "File" in text
        assert "Size: 5 B" in text
        assert "Location:" in text

    def test_properties_text_folder(self, tmp_path):
        ft = make_tree()
        d = os.path.join(os.fspath(tmp_path), "dir")
        os.makedirs(d)
        text = ft.properties_text([d])
        assert "dir" in text
        assert "Folder" in text

    def test_context_menu_has_properties_for_real_files(self, tmp_path):
        ft = tree_at(tmp_path)
        src = os.fspath(tmp_path) + os.sep + "a.txt"
        with open(src, "w") as fh:
            fh.write("x")
        ft.selectPath(src)
        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        assert "properties" in ctx["acts"]
        assert ctx["acts"]["properties"].text() == "Properties…"

    def test_context_menu_hides_properties_for_memory(self, tmp_path):
        ft = tree_at(tmp_path, memory=True)
        menu, ctx = ft._build_context_menu(ft._view.viewport().rect().center())
        assert "properties" not in ctx["acts"]


# --- Sort --------------------------------------------------------------------
class TestSort:
    def test_sort_by_updates_state_and_sorts(self):
        ft = make_tree()
        calls = []
        orig = ft._proxy.sort
        ft._proxy.sort = lambda col, order: calls.append((col, order))
        try:
            ft.sort_by(1, Qt.AscendingOrder)
            assert ft._sort_col == 1
            assert ft._sort_order == Qt.AscendingOrder
            assert calls == [(1, Qt.AscendingOrder)]
            ft.sort_by(3, Qt.DescendingOrder)
            assert ft._sort_col == 3
            assert ft._sort_order == Qt.DescendingOrder
            assert calls[-1] == (3, Qt.DescendingOrder)
        finally:
            ft._proxy.sort = orig

    def test_sort_button_text_reflects_state(self):
        ft = make_tree()
        ft.sort_by(0, Qt.AscendingOrder)
        assert ft.sort_button.text() == "Sort: Name A\u2192Z"
        ft.sort_by(3, Qt.DescendingOrder)
        assert ft.sort_button.text() == "Sort: Type Z\u2192A"

    def test_sort_button_menu_has_all_columns(self, tmp_path):
        ft = tree_at(tmp_path)
        ft._populate_sort_menu()
        labels = [a.text() for a in ft.sort_menu.actions()]

        def has(prefix):
            return any(l.startswith(prefix) for l in labels)

        assert has("Name") and has("Size") and has("Modified") and has("Type")
        # each of the four columns offers both ascending and descending orders
        assert len(labels) == 8
        assert sum(1 for l in labels if l.endswith("A\u2192Z")) == 4

    def test_sort_menu_action_by_size(self, tmp_path):
        ft = tree_at(tmp_path)
        ft._populate_sort_menu()
        size_act = [a for a in ft.sort_menu.actions() if a.text().startswith("Size")][0]
        calls = []
        orig = ft._proxy.sort
        ft._proxy.sort = lambda col, order: calls.append((col, order))
        try:
            size_act.triggered.emit()
        finally:
            ft._proxy.sort = orig
        assert calls == [(1, Qt.AscendingOrder)]

    def test_sort_menu_action_by_type(self, tmp_path):
        ft = tree_at(tmp_path)
        ft._populate_sort_menu()
        type_act = [a for a in ft.sort_menu.actions() if a.text().startswith("Type")][0]
        calls = []
        orig = ft._proxy.sort
        ft._proxy.sort = lambda col, order: calls.append((col, order))
        try:
            type_act.triggered.emit()
        finally:
            ft._proxy.sort = orig
        assert calls == [(3, Qt.AscendingOrder)]

    def test_memory_tree_sorts_by_name_only(self, tmp_path):
        """Memory (encrypted) workspaces expose a single-column tree -> Name sort only."""
        ft = tree_at(tmp_path, memory=True)
        ft._populate_sort_menu()
        labels = [a.text() for a in ft.sort_menu.actions()]
        assert any(l.startswith("Name") for l in labels)
        assert not any(l.startswith("Size") or l.startswith("Type") for l in labels)

    def test_type_sort_folders_first_then_extension(self, tmp_path):
        """'Type' sort = folders first, then files by extension, then by name."""
        import time

        root = os.fspath(tmp_path)
        os.makedirs(os.path.join(root, "zdir"))  # folder whose name sorts last
        for name in ("zeta.py", "alpha.md", "mid.md", "beta.c", "noext", "Gamma.PY"):
            with open(os.path.join(root, name), "w") as fh:
                fh.write("x")

        ft = tree_at(tmp_path)
        ft.sort_by(3, Qt.AscendingOrder)

        # QFileSystemModel loads the path chain and its children asynchronously,
        # so poll until the root node resolves to the right path and all 7
        # entries are visible.
        root = os.fspath(tmp_path)

        def visible_names():
            v = ft._path_index(root)
            if not v.isValid() or os.path.normpath(ft._model_path(v)) != os.path.normpath(root):
                return None
            names = [ft._proxy.data(ft._proxy.index(i, 0, v), Qt.DisplayRole)
                     for i in range(ft._proxy.rowCount(v))]
            return names if len(names) >= 7 else None

        app = QApplication.instance()
        deadline = time.time() + 10.0
        names = None
        while time.time() < deadline:
            names = visible_names()
            if names is not None:
                break
            app.processEvents()
            time.sleep(0.02)

        # folders first; then by extension (".c" < ".md" < ".py"); name as tie-break
        assert names == ["zdir", "noext", "beta.c", "alpha.md", "mid.md", "Gamma.PY", "zeta.py"]

        ft.sort_by(3, Qt.DescendingOrder)
        deadline = time.time() + 10.0
        names_desc = names
        while time.time() < deadline:
            names_desc = visible_names()
            app.processEvents()
            time.sleep(0.02)
            if names_desc == list(reversed(names)):
                break
        assert names_desc == list(reversed(names))


# --- PDF ---------------------------------------------------------------------
class TestPdf:
    def test_classify_pdf_by_extension(self, tmp_path):
        from texteditor.binary import classify_file, KIND_PDF

        p = os.fspath(tmp_path) + os.sep + "doc.pdf"
        with open(p, "wb") as fh:
            fh.write(b"not-really-pdf")
        assert classify_file(p) == KIND_PDF

    def test_classify_pdf_by_magic(self, tmp_path):
        from texteditor.binary import classify_file, KIND_PDF

        p = os.fspath(tmp_path) + os.sep + "weird.bin"
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj")
        assert classify_file(p) == KIND_PDF

    def test_pdf_pane_interface(self, tmp_path):
        from texteditor.pane import Pane
        from texteditor.pdf_view import PdfPane

        p = os.fspath(tmp_path) + os.sep + "doc.pdf"
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj")
        pane = PdfPane(p)
        assert isinstance(pane, Pane)
        assert pane.title == "doc.pdf"
        assert pane.filepath == p
        assert pane.isModified() is False
        assert pane.save() is False
        assert pane.saveAs("/tmp/whatever") is False

    def test_pdf_pane_memory_cleanup(self, tmp_path):
        import os as _os

        from texteditor.pdf_view import PdfPane
        from texteditor.workspace_fs import MemoryTree, WorkspaceFS

        t = MemoryTree()
        data = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj"
        t.write_bytes("docs/report.pdf", data)
        fs = WorkspaceFS(root="/fake", tree=t)
        pane = PdfPane("docs/report.pdf", fs=fs)
        assert pane.title == "report.pdf"
        assert pane.isModified() is False
        # a temp spill was created for the in-memory bytes
        assert pane._tmp is not None and _os.path.isfile(pane._tmp)
        pane._cleanup_tmp()
        assert pane._tmp is None

    def test_make_pane_routes_pdf(self, tmp_path):
        from texteditor.panes import make_pane
        from texteditor.pdf_view import PdfPane
        from texteditor.binary_stub import BinaryPane

        p = os.fspath(tmp_path) + os.sep + "doc.pdf"
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj")
        pane = make_pane(p, theme=None)
        assert isinstance(pane, PdfPane)
        assert not isinstance(pane, BinaryPane)

    def test_workspace_opens_pdf(self, tmp_path):
        from texteditor.workspace import WorkspaceView
        from texteditor.pdf_view import PdfPane
        from texteditor.theme import Theme

        p = os.fspath(tmp_path) + os.sep + "doc.pdf"
        with open(p, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj")
        view = WorkspaceView(None, Theme())
        try:
            pane = view.open_file(p)
            assert isinstance(pane, PdfPane)
            assert pane.filepath == os.path.abspath(p)
            assert view.tab_count() == 1
        finally:
            view.deleteLater()
