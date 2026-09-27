"""Folder sidebar: a QTreeView backed by QFileSystemModel.

- Hides dotfiles / hidden directories (default filter).
- Auto-refreshes on filesystem changes (QFileSystemModel built-in watcher).
- Emits ``fileActivated(path)`` when the user opens a file.
- Emits ``pathDeleted(path)`` after a file or folder is deleted.
- Shows the absolute root path under the header.
- Right-click menu: New File / New Folder / New Todo / New Workspace Here / Copy Path / Move To / Delete.
"""
from __future__ import annotations

import os
import shutil

from PySide6.QtWidgets import (
    QTreeView,
    QFileSystemModel,
    QAbstractItemView,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMenu,
    QInputDialog,
    QFileDialog,
    QMessageBox,
    QApplication,
)
from PySide6.QtGui import QCursor
from PySide6.QtCore import Signal, QDir, Qt

from .todos import Todos, TodosStore


class FileTree(QWidget):
    fileActivated = Signal(str)
    toggleWorkspacesRequested = Signal()
    pathDeleted = Signal(str)
    revealInExplorerRequested = Signal(str)
    openInGroupRequested = Signal(str, str)   # (path, "left"|"right")
    createWorkspaceRequested = Signal(str)    # (folder path)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QLabel("Explorer", self)
        self.header.setStyleSheet("padding: 6px 10px 0px 10px; font-weight: bold; color: #999; background: #252526;")
        layout.addWidget(self.header)

        self.root_label = QLabel("", self)
        self.root_label.setStyleSheet("padding: 2px 10px 4px 10px; color: #808080; font-size: 11px;")
        self.root_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root_label.setToolTip("")
        layout.addWidget(self.root_label)

        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.Files | QDir.Dirs | QDir.NoDotAndDotDot)
        self._model.setRootPath("")

        self._view = QTreeView(self)
        self._view.setModel(self._model)
        self._view.setAnimated(True)
        self._view.setUniformRowHeights(True)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setHeaderHidden(True)
        root_idx = self._model.index("")
        for col in range(1, self._model.columnCount(root_idx)):
            self._view.setColumnHidden(col, True)
        self._view.setIndentation(16)
        self._view.setExpandsOnDoubleClick(True)
        self._view.setRootIsDecorated(True)

        self._view.activated.connect(self._on_activated)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)

        layout.addWidget(self._view)

        bottom = QWidget(self)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(8, 6, 8, 6)
        self.workspaces_btn = QPushButton("Workspaces", bottom)
        self.workspaces_btn.setToolTip("Show or hide the workspaces list")
        bl.addWidget(self.workspaces_btn)
        self.workspaces_btn.clicked.connect(self.toggleWorkspacesRequested.emit)
        layout.addWidget(bottom)

        self._root_path: str | None = None

    def _on_activated(self, index) -> None:
        path = self._model.filePath(index)
        if path and os.path.isfile(path):
            self.fileActivated.emit(path)

    def selected_paths(self) -> list[str]:
        sm = self._view.selectionModel()
        if sm is None:
            return []
        out = []
        for idx in sm.selectedRows(0):
            p = self._model.filePath(idx)
            if p:
                out.append(p)
        return out

    # --- right-click context menu ----------------------------------------------
    def _context_selection(self, pos) -> list[str]:
        sel = self.selected_paths()
        if not sel:
            idx = self._view.indexAt(pos)
            p = self._model.filePath(idx) if idx.isValid() else ""
            sel = [p] if p else []
        return sel

    def _new_target(self, sel: list[str]) -> str | None:
        for p in sel:
            if os.path.isdir(p):
                return p
        for p in sel:
            if os.path.isfile(p):
                return os.path.dirname(p)
        return self._root_path

    def _on_context_menu(self, pos) -> None:
        sel = self._context_selection(pos)
        target = self._new_target(sel) if sel else self._root_path
        if not target or not os.path.isdir(target):
            return
        files = [p for p in sel if os.path.isfile(p)]
        folder = next((p for p in sel if os.path.isdir(p)), None)

        menu = QMenu(self)
        act_file = menu.addAction("New File…")
        act_folder = menu.addAction("New Folder…")
        act_todo = menu.addAction("New Todo…")
        act_ws = menu.addAction("New Workspace Here")
        act_ws.setEnabled(folder is not None)
        menu.addSeparator()
        act_copy = menu.addAction("Copy Absolute Path")
        act_copy.setEnabled(bool(sel))
        act_reveal = menu.addAction("Show in File Explorer")
        act_reveal.setEnabled(bool(sel))
        act_open = None
        act_open_left = None
        act_open_right = None
        if files:
            act_open = menu.addAction("Open")
            menu.addSeparator()
            act_open_left = menu.addAction("Open in the Left")
            act_open_right = menu.addAction("Open at the Right")
        act_move = None
        if files:
            menu.addSeparator()
            act_move = menu.addAction("Move To…")
        act_delete = None
        if sel:
            menu.addSeparator()
            act_delete = menu.addAction("Delete…")

        chosen = menu.exec(QCursor.pos())
        if chosen is act_file:
            name, ok = QInputDialog.getText(self, "New File", "File name:", text="untitled.txt")
            if ok:
                self.new_file_in(target, name)
        elif chosen is act_folder:
            name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="new folder")
            if ok:
                self.new_folder_in(target, name)
        elif chosen is act_todo:
            name, ok = QInputDialog.getText(self, "New Todo", "File name:", text="todos.todo")
            if ok:
                if self.new_todo_in(target, name) is None:
                    QMessageBox.warning(self, "New Todo", "Could not create the Todo file.")
        elif chosen is act_ws:
            self.create_workspace_here(folder)
        elif chosen is act_copy:
            if not self.copy_selected_paths(sel):
                self.copy_path_to_clipboard(self._root_path or "")
        elif chosen is act_reveal:
            self.reveal_selected(sel)
        elif chosen is act_open:
            self.open_selected(sel)
        elif chosen is act_open_left:
            for p in files:
                self.openInGroupRequested.emit(p, "left")
        elif chosen is act_open_right:
            for p in files:
                self.openInGroupRequested.emit(p, "right")
        elif chosen is act_move:
            dest = QFileDialog.getExistingDirectory(self, "Move to folder", os.path.dirname(files[0]))
            if dest:
                moved = self.move_selected_to(dest, sel)
                if not moved:
                    QMessageBox.warning(self, "Move", "Could not move any selected file.")
        elif chosen is act_delete:
            n = len(sel)
            ret = QMessageBox.question(
                self,
                "Delete",
                f"Delete {n} item(s)?\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                deleted = self.delete_selected(sel)
                if len(deleted) < n:
                    QMessageBox.warning(self, "Delete", "Some items could not be deleted.")

    # --- filesystem actions -----------------------------------------------------
    def new_file_in(self, dir_path: str, name: str) -> str | None:
        """Create an empty file in ``dir_path``; select it and emit fileActivated."""
        name = (name or "").strip()
        if not name:
            return None
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            return None
        try:
            with open(path, "a", encoding="utf-8"):
                pass
        except OSError:
            return None
        self.selectPath(path)
        self.fileActivated.emit(path)
        return path

    def new_folder_in(self, dir_path: str, name: str) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            return None
        try:
            os.makedirs(path)
        except OSError:
            return None
        self.selectPath(path)
        return path

    def new_todo_in(self, dir_path: str, name: str) -> str | None:
        """Create a new Todos JSON document in ``dir_path``; select and open it."""
        name = (name or "").strip()
        if not name:
            return None
        if not name.lower().endswith(".todo"):
            name += ".todo"
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            return None
        try:
            todos = Todos()
            todos.add_category("General")
            TodosStore().save(todos, path)
        except (OSError, ValueError):
            return None
        self.selectPath(path)
        self.fileActivated.emit(path)
        return path

    def copy_path_to_clipboard(self, path: str) -> bool:
        if not path:
            return False
        QApplication.clipboard().setText(os.path.abspath(path))
        return True

    # --- multi-selection actions (operate on a set of paths) -------------------
    def _resolve_paths(self, paths: list[str] | None) -> list[str]:
        base = self.selected_paths() if paths is None else list(paths)
        return [p for p in base if p]

    def copy_selected_paths(self, paths: list[str] | None = None) -> bool:
        sel = self._resolve_paths(paths)
        if not sel:
            return False
        QApplication.clipboard().setText("\n".join(os.path.abspath(p) for p in sel))
        return True

    def open_selected(self, paths: list[str] | None = None) -> list[str]:
        sel = [p for p in self._resolve_paths(paths) if os.path.isfile(p)]
        for p in sel:
            self.fileActivated.emit(p)
        return sel

    def reveal_selected(self, paths: list[str] | None = None) -> list[str]:
        """Emit ``revealInExplorerRequested`` for each existing selected path."""
        sel = [p for p in self._resolve_paths(paths) if os.path.exists(p)]
        for p in sel:
            self.revealInExplorerRequested.emit(p)
        return sel

    def create_workspace_here(self, folder: str) -> bool:
        """Emit ``createWorkspaceRequested`` for a selected folder path."""
        if not folder or not os.path.isdir(folder):
            return False
        self.createWorkspaceRequested.emit(folder)
        return True

    def move_selected_to(self, dest_dir: str, paths: list[str] | None = None) -> list[str]:
        out = []
        for p in self._resolve_paths(paths):
            new = self.move_file_to(p, dest_dir)
            if new:
                out.append(new)
        return out

    def delete_selected(self, paths: list[str] | None = None) -> list[str]:
        out = []
        for p in self._resolve_paths(paths):
            if os.path.isfile(p):
                if self.delete_file(p):
                    out.append(p)
            elif os.path.isdir(p):
                if self.delete_folder(p):
                    out.append(p)
        return out

    def move_file_to(self, src_path: str, dest_dir: str) -> str | None:
        """Move an existing file into ``dest_dir``; returns the new path."""
        if not src_path or not os.path.isfile(src_path) or not dest_dir:
            return None
        src_path = os.path.abspath(src_path)
        dest_dir = os.path.abspath(dest_dir)
        if dest_dir == os.path.dirname(src_path):
            return None
        try:
            shutil.move(src_path, dest_dir)
        except OSError:
            return None
        new_path = os.path.join(dest_dir, os.path.basename(src_path))
        self.selectPath(new_path)
        return new_path

    def delete_file(self, path: str) -> bool:
        """Delete a file (never the root). Emits ``pathDeleted`` on success."""
        if not path:
            return False
        path = os.path.abspath(path)
        if not os.path.isfile(path) or path == self._root_path:
            return False
        try:
            os.remove(path)
        except OSError:
            return False
        self.pathDeleted.emit(path)
        return True

    def delete_folder(self, path: str) -> bool:
        """Delete a folder tree (never the root or anything containing it)."""
        if not path:
            return False
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return False
        root = os.path.abspath(self._root_path) if self._root_path else None
        if root and (path == root or root.startswith(path + os.sep)):
            return False
        try:
            shutil.rmtree(path)
        except OSError:
            return False
        self.pathDeleted.emit(path)
        return True

    def setRoot(self, path: str) -> None:
        self._root_path = path
        self.root_label.setText(path)
        self.root_label.setToolTip(path)
        self._model.setRootPath(path)
        index = self._model.index(path)
        self._view.setRootIndex(index)
        self._view.expand(index)
        # keep the view showing the root folder itself
        self._view.setCurrentIndex(index)

    def currentRoot(self) -> str | None:
        return self._root_path

    def selectPath(self, path: str) -> None:
        index = self._model.index(path)
        if not index.isValid():
            return
        # expand the ancestor folders (up to the tree root) so the node is visible
        root_index = self._view.rootIndex()
        ancestor = index.parent()
        while ancestor.isValid() and ancestor != root_index:
            self._view.expand(ancestor)
            ancestor = ancestor.parent()
        self._view.setCurrentIndex(index)
        self._view.scrollTo(index)
