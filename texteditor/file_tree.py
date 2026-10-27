"""Folder sidebar: a QTreeView backed by QFileSystemModel (or MemoryTreeModel).

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
    QLabel,
    QMenu,
    QInputDialog,
    QFileDialog,
    QMessageBox,
    QApplication,
    QLineEdit,
)
from PySide6.QtGui import QCursor, QShortcut, QKeySequence
from PySide6.QtCore import Signal, QDir, Qt, QModelIndex, QSortFilterProxyModel, QUrl, QMimeData

from .todos import Todos, TodosStore
from .memory_tree_model import MemoryTreeModel


class _FileExplorerView(QTreeView):
    """Tree view with drag-and-drop support for files and folders.

    Drag one or more rows and drop them onto a folder row (or the empty area,
    which targets the workspace root) to move them there. Hold **Ctrl** while
    dropping to copy instead of move. This view only translates the gesture
    into a list of paths plus a destination folder; the filesystem work is
    delegated to the parent :class:`FileTree` (:meth:`FileTree.drop_files`).
    """

    def __init__(self, tree, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    # -- drag source --------------------------------------------------------
    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        paths = [self._tree._model_path(i) for i in indexes]
        paths = [os.path.abspath(p) for p in paths if p]
        if not paths:
            return None
        lines = [QUrl.fromLocalFile(p).toString() for p in paths]
        mime = QMimeData()
        mime.setData("text/uri-list", "\n".join(lines).encode("utf-8"))
        return mime

    # -- drop target --------------------------------------------------------
    def _drop_point(self, event):
        try:
            return event.position().toPoint()
        except AttributeError:
            return event.pos()

    def _drop_paths(self, mime) -> list[str]:
        if mime is None or not mime.hasFormat("text/uri-list"):
            return []
        data = bytes(mime.data("text/uri-list")).decode("utf-8", "replace")
        out = []
        for line in data.splitlines():
            line = line.strip()
            if not line or line.startswith(("http://", "https://")):
                continue
            if line.startswith("file://"):
                out.append(QUrl(line).toLocalFile())
            else:
                out.append(line)
        return [p for p in out if p]

    def _drop_target(self, event):
        idx = self.indexAt(self._drop_point(event))
        if idx.isValid():
            p = self._tree._model_path(idx)
            if p:
                return p if self._tree._is_dir(p) else os.path.dirname(p)
        return self._tree.currentRoot()

    def dragEnterEvent(self, event) -> None:
        if self._drop_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._drop_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._drop_paths(event.mimeData())
        target = self._drop_target(event)
        if not paths or not target:
            event.ignore()
            return
        copy = (event.action() == Qt.CopyAction) or bool(event.modifiers() & Qt.ControlModifier)
        if self._tree.drop_files(paths, target, copy):
            event.acceptProposedAction()
        else:
            event.ignore()


class FileTree(QWidget):
    fileActivated = Signal(str)
    pathDeleted = Signal(str)
    revealInExplorerRequested = Signal(str)
    openInGroupRequested = Signal(str, str)   # (path, "left"|"right")
    createWorkspaceRequested = Signal(str)    # (folder path)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fs = None  # WorkspaceFS; when set, all I/O goes through it
        self._is_memory = False

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

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Filter files…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setStyleSheet(
            "padding: 3px 6px; margin: 2px 6px 4px 6px; font-size: 12px;"
        )
        layout.addWidget(self.search_box)

        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.Files | QDir.Dirs | QDir.NoDotAndDotDot)
        self._model.setRootPath("")
        self._fs_model = self._model  # persistent QFileSystemModel (restored when leaving memory mode)

        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self._proxy.setFilterKeyColumn(0)
        self.search_box.textChanged.connect(self._proxy.setFilterFixedString)

        self._view = _FileExplorerView(self, self)
        self._view.setModel(self._proxy)
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

        # Vault section (mixed state): read-only view of unlocked vault contents.
        self._vault_label = QLabel("\U0001F513 Vault", self)
        self._vault_label.setStyleSheet(
            "padding: 4px 10px 2px 10px; font-weight: bold; color: #6aa9e0;")
        self._vault_label.hide()

        self._vault_model = QFileSystemModel(self)
        self._vault_model.setFilter(QDir.Files | QDir.Dirs | QDir.NoDotAndDotDot)
        self._vault_model.setRootPath("")

        self._vault_view = QTreeView(self)
        self._vault_view.setModel(self._vault_model)
        self._vault_view.setAnimated(True)
        self._vault_view.setUniformRowHeights(True)
        self._vault_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._vault_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._vault_view.setHeaderHidden(True)
        vroot = self._vault_model.index("")
        for col in range(1, self._vault_model.columnCount(vroot)):
            self._vault_view.setColumnHidden(col, True)
        self._vault_view.setIndentation(16)
        self._vault_view.setExpandsOnDoubleClick(True)
        self._vault_view.setRootIsDecorated(True)
        self._vault_view.setContextMenuPolicy(Qt.NoContextMenu)
        self._vault_view.activated.connect(self._on_vault_activated)
        self._vault_view.hide()

        layout.addWidget(self._vault_label)
        layout.addWidget(self._vault_view)

        self._root_path: str | None = None

        # Internal clipboard for file copy/cut/paste (independent of the system
        # text clipboard, which is used for "Copy Absolute Path").
        self._file_clipboard: list[str] = []
        self._file_clipboard_cut: bool = False

        # Keyboard shortcuts active while the tree has focus. Copy/Cut/Paste are
        # routed from the main window (they collide with the editor's actions);
        # these are the additional file-explorer bindings.
        sc = (
            (QKeySequence.Delete, self.delete_files, "Delete the selected item(s)"),
            (QKeySequence("F2"), self.rename_file, "Rename the selected item"),
            (QKeySequence("Ctrl+Shift+N"), self.new_folder_here, "New folder in the selected location"),
        )
        self._shortcuts = []
        for key, slot, tip in sc:
            s = QShortcut(key, self._view)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(slot)
            self._shortcuts.append(s)

    def has_tree_focus(self) -> bool:
        """True when keyboard focus is on the (writable) explorer tree view."""
        w = QApplication.focusWidget()
        if w is None:
            return False
        node = w
        while node is not None:
            if node is self._view:
                return True
            node = node.parentWidget()
        return False

    # --- file clipboard (copy / cut / paste) ----------------------------------
    def copy_files(self) -> bool:
        """Copy the selected files/folders to the internal clipboard."""
        sel = [p for p in self.selected_paths() if self._exists(p)]
        if not sel:
            return False
        self._file_clipboard = [os.path.abspath(p) for p in sel]
        self._file_clipboard_cut = False
        return True

    def cut_files(self) -> bool:
        """Cut the selected files/folders to the internal clipboard."""
        sel = [p for p in self.selected_paths() if self._exists(p)]
        if not sel:
            return False
        self._file_clipboard = [os.path.abspath(p) for p in sel]
        self._file_clipboard_cut = True
        return True

    def paste_files(self) -> bool:
        """Paste the internal clipboard into the selected folder (or the root)."""
        if not self._file_clipboard:
            return False
        sel = self.selected_paths()
        dir_sel = [p for p in sel if self._is_dir(p)]
        if dir_sel:
            target = os.path.abspath(dir_sel[0])
        elif sel:
            target = os.path.dirname(os.path.abspath(sel[0]))
        else:
            target = os.path.abspath(self._root_path) if self._root_path else None
        if not target or not self._is_dir(target):
            return False
        ok = False
        for src in list(self._file_clipboard):
            if self._exists(src) and self._paste_one(src, target):
                ok = True
        if self._file_clipboard_cut:
            self._file_clipboard = []
            self._file_clipboard_cut = False
        return ok

    def drop_files(self, paths: list[str], target_dir: str, copy: bool) -> bool:
        """Move or copy each of *paths* into *target_dir* (drag-and-drop).

        Returns True if at least one item was moved or copied.
        """
        target_dir = os.path.abspath(target_dir)
        if not self._is_dir(target_dir):
            return False
        moved = False
        for src in paths:
            if not src:
                continue
            if self._drop_one(os.path.abspath(src), target_dir, copy):
                moved = True
        return moved

    def _drop_one(self, src: str, target_dir: str, copy: bool) -> bool:
        if not self._exists(src) or not self._is_dir(target_dir):
            return False
        if src == target_dir:
            return False
        if self._is_dir(src) and target_dir.startswith(src + os.sep):
            return False
        if os.path.dirname(src) == target_dir:
            return False
        base = os.path.basename(src.rstrip(os.sep))
        if not base:
            return False
        dest = self._unique_dest(os.path.join(target_dir, base))
        try:
            if self._is_dir(src):
                if copy:
                    self._copy_dir(src, dest)
                else:
                    self._move_dir(src, dest)
            else:
                if copy:
                    self._copy_file(src, dest)
                else:
                    self._move_file(src, dest)
        except OSError:
            return False
        self._refresh_model()
        self.selectPath(dest)
        return True

    def delete_files(self) -> None:
        """Delete the selected files/folders (with confirmation)."""
        sel = [p for p in self.selected_paths() if self._exists(p)]
        if not sel:
            return
        n = len(sel)
        ret = QMessageBox.question(
            self, "Delete",
            f"Delete {n} item(s)?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        deleted = self.delete_selected(sel)
        if len(deleted) < n:
            QMessageBox.warning(self, "Delete", "Some items could not be deleted.")

    def rename_file(self) -> None:
        """Rename the single selected item."""
        sel = [p for p in self.selected_paths() if self._exists(p)]
        if len(sel) == 1:
            self.rename_selected(sel)

    def new_folder_here(self) -> None:
        sel = self.selected_paths()
        target = self._new_target(sel) if sel else self._root_path
        if not target or not self._is_dir(target):
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="new folder")
        if ok:
            self.new_folder_in(target, name)

    def select_all_files(self) -> None:
        """Select every visible row in the explorer tree."""
        self._view.selectAll()
        self._view.setFocus()

    def _paste_one(self, src: str, target_dir: str) -> bool:
        src = os.path.abspath(src)
        target_dir = os.path.abspath(target_dir)
        if src == target_dir:
            return False
        # Never paste a folder into itself or one of its subfolders.
        if self._is_dir(src) and target_dir.startswith(src + os.sep):
            return False
        base = os.path.basename(src.rstrip(os.sep))
        if not base:
            return False
        dest = self._unique_dest(os.path.join(target_dir, base))
        try:
            if self._is_dir(src):
                if self._file_clipboard_cut:
                    self._move_dir(src, dest)
                else:
                    self._copy_dir(src, dest)
            else:
                if self._file_clipboard_cut:
                    self._move_file(src, dest)
                else:
                    self._copy_file(src, dest)
        except OSError:
            return False
        self._refresh_model()
        self.selectPath(dest)
        return True

    def _unique_dest(self, path: str) -> str:
        """Return ``path``, or ``path (n)`` with the smallest free ``n``."""
        if not self._exists(path):
            return path
        root, ext = os.path.splitext(path)
        i = 1
        while self._exists(f"{root} ({i}){ext}"):
            i += 1
        return f"{root} ({i}){ext}"

    def _copy_file(self, src: str, dest: str) -> None:
        if self._fs is not None:
            self._fs.write_bytes(dest, self._fs.read_bytes(src))
        else:
            shutil.copy2(src, dest)

    def _copy_dir(self, src: str, dest: str) -> None:
        if self._fs is not None:
            self._fs.makedirs(dest)
            for name in self._fs.listdir(src):
                sub_src = os.path.join(src, name)
                sub_dest = os.path.join(dest, name)
                if self._fs.isdir(sub_src):
                    self._copy_dir(sub_src, sub_dest)
                else:
                    self._copy_file(sub_src, sub_dest)
        else:
            shutil.copytree(src, dest)

    def _move_file(self, src: str, dest: str) -> None:
        if self._fs is not None:
            self._fs.rename(src, dest)
        else:
            shutil.move(src, dest)

    def _move_dir(self, src: str, dest: str) -> None:
        if self._fs is not None:
            self._fs.rename(src, dest)
        else:
            shutil.move(src, dest)

    # --- FS abstraction helpers --------------------------------------------------
    def _is_file(self, path: str) -> bool:
        if self._fs is not None:
            return self._fs.isfile(path)
        return os.path.isfile(path)

    def _is_dir(self, path: str) -> bool:
        if self._fs is not None:
            return self._fs.isdir(path)
        return os.path.isdir(path)

    def _exists(self, path: str) -> bool:
        if self._fs is not None:
            return self._fs.exists(path)
        return os.path.exists(path)

    def _path_index(self, path: str) -> QModelIndex:
        """Get a proxy QModelIndex from an absolute path, works for both model types."""
        if hasattr(self._model, "index_by_path"):
            src = self._model.index_by_path(path)
        else:
            src = self._model.index(path)
        if not src.isValid():
            return QModelIndex()
        return self._proxy.mapFromSource(src)

    def _model_path(self, index) -> str:
        """Get the absolute path for a (proxy) index from the view."""
        src = self._proxy.mapToSource(index)
        return self._model.filePath(src)

    def set_fs(self, fs) -> None:
        """Set the WorkspaceFS. Swap to MemoryTreeModel for memory workspaces,
        restore the QFileSystemModel otherwise."""
        self._fs = fs
        self._is_memory = fs is not None and fs.is_memory
        if self._is_memory and self._root_path:
            self._switch_to_memory_model(self._root_path)
        elif self._model is not self._fs_model:
            self._restore_fs_model()

    def _restore_fs_model(self) -> None:
        """Restore the QFileSystemModel as the active model (non-encrypted workspace)."""
        if self._model is self._fs_model:
            return
        self._proxy.setSourceModel(self._fs_model)
        self._view.setHeaderHidden(True)
        root_idx = self._fs_model.index("")
        for col in range(1, self._fs_model.columnCount(root_idx)):
            self._view.setColumnHidden(col, True)
        self._model = self._fs_model

    def _switch_to_memory_model(self, root: str) -> None:
        """Replace the QFileSystemModel with a MemoryTreeModel."""
        tree = self._fs.tree
        model = MemoryTreeModel(tree, root, self)
        self._proxy.setSourceModel(model)
        self._view.setHeaderHidden(True)
        vroot = model.index(0, 0)
        for col in range(1, model.columnCount(vroot)):
            self._view.setColumnHidden(col, True)
        self._model = model
        index = model.index_by_path(root)
        self._view.setRootIndex(index)
        if index.isValid():
            self._view.expand(index)
            self._view.setCurrentIndex(index)

    # --- activation / selection --------------------------------------------------
    def _on_activated(self, index) -> None:
        path = self._model_path(index)
        if path and self._is_file(path):
            self.fileActivated.emit(path)

    def selected_paths(self) -> list[str]:
        sm = self._view.selectionModel()
        if sm is None:
            return []
        out = []
        for idx in sm.selectedRows(0):
            p = self._model_path(idx)
            if p:
                out.append(p)
        return out

    # --- right-click context menu ----------------------------------------------
    def _context_selection(self, pos) -> list[str]:
        sel = self.selected_paths()
        if not sel:
            idx = self._view.indexAt(pos)
            p = self._model_path(idx) if idx.isValid() else ""
            sel = [p] if p else []
        return sel

    def _new_target(self, sel: list[str]) -> str | None:
        for p in sel:
            if self._is_dir(p):
                return p
        for p in sel:
            if self._is_file(p):
                return os.path.dirname(p)
        return self._root_path

    def _build_context_menu(self, pos):
        """Build the right-click menu. Returns ``(menu, ctx)`` or ``(None, None)``
        when the menu does not apply. ``ctx`` carries the selection, the target
        folder and a dict of the created actions, so the dispatch and tests can
        work without showing the (modal) menu."""
        sel = self._context_selection(pos)
        target = self._new_target(sel) if sel else self._root_path
        if not target or not self._is_dir(target):
            return None, None
        files = [p for p in sel if self._is_file(p)]
        folder = next((p for p in sel if self._is_dir(p)), None)

        menu = QMenu(self)
        acts = {}
        acts["file"] = menu.addAction("New File…")
        acts["folder"] = menu.addAction("New Folder…")
        acts["todo"] = menu.addAction("New Todo…")
        acts["ws"] = menu.addAction("New Workspace Here")
        acts["ws"].setEnabled(folder is not None)
        menu.addSeparator()
        acts["copy"] = menu.addAction("Copy")
        acts["copy"].setEnabled(bool(sel))
        acts["cut"] = menu.addAction("Cut")
        acts["cut"].setEnabled(bool(sel))
        acts["paste"] = menu.addAction("Paste")
        acts["paste"].setEnabled(bool(self._file_clipboard))
        menu.addSeparator()
        acts["copy_path"] = menu.addAction("Copy Absolute Path")
        acts["copy_path"].setEnabled(bool(sel))
        acts["reveal"] = menu.addAction("Show in File Explorer")
        acts["reveal"].setEnabled(bool(sel))
        if files:
            acts["open"] = menu.addAction("Open")
            menu.addSeparator()
            acts["open_left"] = menu.addAction("Open in the Left")
            acts["open_right"] = menu.addAction("Open at the Right")
        if sel:
            menu.addSeparator()
            acts["rename"] = menu.addAction("Rename…")
            acts["rename"].setEnabled(len(sel) == 1)
        if files:
            acts["move"] = menu.addAction("Move To…")
        if sel:
            menu.addSeparator()
            acts["delete"] = menu.addAction("Delete…")

        ctx = {"sel": sel, "target": target, "files": files, "folder": folder, "acts": acts}
        return menu, ctx

    def _on_context_menu(self, pos) -> None:
        menu, ctx = self._build_context_menu(pos)
        if menu is None:
            return
        chosen = menu.exec(QCursor.pos())
        if chosen is not None:
            self._dispatch_context(chosen, ctx)

    def _dispatch_context(self, chosen, ctx) -> None:
        acts = ctx["acts"]
        sel = ctx["sel"]
        target = ctx["target"]
        files = ctx["files"]
        folder = ctx["folder"]
        if chosen is acts.get("file"):
            name, ok = QInputDialog.getText(self, "New File", "File name:", text="untitled.txt")
            if ok:
                self.new_file_in(target, name)
        elif chosen is acts.get("folder"):
            name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="new folder")
            if ok:
                self.new_folder_in(target, name)
        elif chosen is acts.get("todo"):
            name, ok = QInputDialog.getText(self, "New Todo", "File name:", text="todos.todo")
            if ok:
                if self.new_todo_in(target, name) is None:
                    QMessageBox.warning(self, "New Todo", "Could not create the Todo file.")
        elif chosen is acts.get("ws"):
            self.create_workspace_here(folder)
        elif chosen is acts.get("copy"):
            self.copy_files()
        elif chosen is acts.get("cut"):
            self.cut_files()
        elif chosen is acts.get("paste"):
            if not self.paste_files():
                QMessageBox.information(self, "Paste", "Nothing to paste here.")
        elif chosen is acts.get("copy_path"):
            if not self.copy_selected_paths(sel):
                self.copy_path_to_clipboard(self._root_path or "")
        elif chosen is acts.get("reveal"):
            self.reveal_selected(sel)
        elif chosen is acts.get("open"):
            self.open_selected(sel)
        elif chosen is acts.get("open_left"):
            for p in files:
                self.openInGroupRequested.emit(p, "left")
        elif chosen is acts.get("open_right"):
            for p in files:
                self.openInGroupRequested.emit(p, "right")
        elif chosen is acts.get("rename"):
            self.rename_selected(sel)
        elif chosen is acts.get("move"):
            dest = QFileDialog.getExistingDirectory(self, "Move to folder", os.path.dirname(files[0]))
            if dest:
                moved = self.move_selected_to(dest, sel)
                if not moved:
                    QMessageBox.warning(self, "Move", "Could not move any selected file.")
        elif chosen is acts.get("delete"):
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
        if self._exists(path):
            return None
        try:
            if self._fs is not None:
                self._fs.write_bytes(path, b"")
            else:
                with open(path, "a", encoding="utf-8"):
                    pass
        except OSError:
            return None
        self._refresh_model()
        self.selectPath(path)
        self.fileActivated.emit(path)
        return path

    def new_folder_in(self, dir_path: str, name: str) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        path = os.path.join(dir_path, name)
        if self._exists(path):
            return None
        try:
            if self._fs is not None:
                self._fs.mkdir(path)
            else:
                os.makedirs(path)
        except OSError:
            return None
        self._refresh_model()
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
        if self._exists(path):
            return None
        try:
            todos = Todos()
            todos.add_category("General")
            store = TodosStore(fs=self._fs)
            store.save(todos, path)
        except (OSError, ValueError):
            return None
        self._refresh_model()
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
        sel = [p for p in self._resolve_paths(paths) if self._is_file(p)]
        for p in sel:
            self.fileActivated.emit(p)
        return sel

    def reveal_selected(self, paths: list[str] | None = None) -> list[str]:
        """Emit ``revealInExplorerRequested`` for each existing selected path."""
        sel = [p for p in self._resolve_paths(paths) if self._exists(p)]
        for p in sel:
            self.revealInExplorerRequested.emit(p)
        return sel

    def create_workspace_here(self, folder: str) -> bool:
        """Emit ``createWorkspaceRequested`` for a selected folder path."""
        if not folder or not self._is_dir(folder):
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
            if self._is_file(p):
                if self.delete_file(p):
                    out.append(p)
            elif self._is_dir(p):
                if self.delete_folder(p):
                    out.append(p)
        return out

    def move_file_to(self, src_path: str, dest_dir: str) -> str | None:
        """Move an existing file into ``dest_dir``; returns the new path."""
        if not src_path or not self._is_file(src_path) or not dest_dir:
            return None
        src_path = os.path.abspath(src_path)
        dest_dir = os.path.abspath(dest_dir)
        if dest_dir == os.path.dirname(src_path):
            return None
        try:
            if self._fs is not None:
                new_path = os.path.join(dest_dir, os.path.basename(src_path))
                self._fs.rename(src_path, new_path)
            else:
                shutil.move(src_path, dest_dir)
                new_path = os.path.join(dest_dir, os.path.basename(src_path))
        except OSError:
            return None
        self._refresh_model()
        self.selectPath(new_path)
        return new_path

    def rename_selected(self, paths: list[str]) -> str | None:
        """Rename a single selected file or folder. Returns the new path on success."""
        if len(paths) != 1:
            return None
        old_path = os.path.abspath(paths[0])
        if old_path == self._root_path:
            return None
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if not ok:
            return None
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return None
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if self._exists(new_path):
            QMessageBox.warning(self, "Rename", "A file or folder with that name already exists.")
            return None
        try:
            if self._fs is not None:
                self._fs.rename(old_path, new_path)
            else:
                os.rename(old_path, new_path)
        except OSError:
            return None
        self._refresh_model()
        self.selectPath(new_path)
        return new_path

    def delete_file(self, path: str) -> bool:
        """Delete a file (never the root). Emits ``pathDeleted`` on success."""
        if not path:
            return False
        path = os.path.abspath(path)
        if not self._is_file(path) or path == self._root_path:
            return False
        try:
            if self._fs is not None:
                self._fs.delete(path)
            else:
                os.remove(path)
        except OSError:
            return False
        self._refresh_model()
        self.pathDeleted.emit(path)
        return True

    def delete_folder(self, path: str) -> bool:
        """Delete a folder tree (never the root or anything containing it)."""
        if not path:
            return False
        path = os.path.abspath(path)
        if not self._is_dir(path):
            return False
        root = os.path.abspath(self._root_path) if self._root_path else None
        if root and (path == root or root.startswith(path + os.sep)):
            return False
        try:
            if self._fs is not None:
                self._fs.delete_dir(path)
            else:
                shutil.rmtree(path)
        except OSError:
            return False
        self._refresh_model()
        self.pathDeleted.emit(path)
        return True

    def setRoot(self, path: str) -> None:
        self._root_path = path
        self.root_label.setText(path)
        self.root_label.setToolTip(path)
        if self._is_memory and self._fs is not None:
            self._switch_to_memory_model(path)
        else:
            self._model.setRootPath(path)
            index = self._path_index(path)
            self._view.setRootIndex(index)
            self._view.expand(index)
            self._view.setCurrentIndex(index)

    def set_show_hidden(self, on: bool) -> None:
        """Toggle visibility of dotfiles in the tree."""
        if on:
            self._fs_model.setFilter(QDir.Files | QDir.Dirs | QDir.Hidden)
        else:
            self._fs_model.setFilter(QDir.Files | QDir.Dirs | QDir.NoDotAndDotDot)
        self._proxy.setSourceModel(self._fs_model)
        if self._root_path:
            index = self._path_index(self._root_path)
            self._view.setRootIndex(index)
            self._view.expand(index)

    def currentRoot(self) -> str | None:
        return self._root_path

    def _refresh_model(self) -> None:
        """Refresh the tree model after filesystem mutations."""
        if self._is_memory and self._fs is not None:
            # Rebuild the memory tree model from the updated tree
            root = self._root_path
            if root:
                self._switch_to_memory_model(root)
        # QFileSystemModel auto-refreshes via its watcher

    def set_vault_root(self, path: str) -> None:
        """Show the vault section rooted at ``path`` (unlocked temp dir)."""
        if not path:
            self.clear_vault_root()
            return
        self._vault_model.setRootPath(path)
        index = self._vault_model.index(path)
        self._vault_view.setRootIndex(index)
        self._vault_view.expand(index)
        self._vault_label.show()
        self._vault_view.show()

    def clear_vault_root(self) -> None:
        """Hide the vault section."""
        self._vault_model.setRootPath("")
        self._vault_view.setRootIndex(self._vault_model.index(""))
        self._vault_label.hide()
        self._vault_view.hide()

    def _on_vault_activated(self, index) -> None:
        path = self._vault_model.filePath(index)
        if path and os.path.isfile(path):
            self.fileActivated.emit(path)

    def selectPath(self, path: str) -> None:
        index = self._path_index(path)
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
