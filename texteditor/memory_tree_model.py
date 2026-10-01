"""QAbstractItemModel backed by MemoryTree for encrypted (in-memory) workspaces.

Provides the same interface subset that FileTree uses from QFileSystemModel:
- ``filePath(index)`` → absolute path string
- ``index_by_path(path)`` → QModelIndex
- ``setRootPath(path)`` → no-op (root is fixed at construction)
- Standard item model methods for QTreeView
"""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QObject, Qt
from PySide6.QtWidgets import QFileIconProvider

from .workspace_fs import MemoryTree

PATH_ROLE = Qt.UserRole + 1
IS_DIR_ROLE = Qt.UserRole + 2


class MemoryTreeModel(QAbstractItemModel):
    """Tree model over an in-memory file tree."""

    def __init__(self, tree: MemoryTree, root: str, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree
        self._root = os.path.abspath(root)
        # Each node is a QObject whose "rel" property holds its relative path.
        # QObjects are safe to use as QModelIndex internal pointers (unlike raw
        # Python objects, which PySide does not keep alive). The model keeps
        # them alive so their pointers never dangle.
        self._node_by_rel: dict[str, QObject] = {}
        self._nodes: list[QObject] = []
        # Folder/file icons so encrypted workspaces match plain ones visually
        # (QFileSystemModel provides these automatically; we do it manually).
        _provider = QFileIconProvider()
        self._folder_icon = _provider.icon(QFileIconProvider.Folder)
        self._file_icon = _provider.icon(QFileIconProvider.File)

    def _node_for(self, rel: str) -> QObject:
        node = self._node_by_rel.get(rel)
        if node is None:
            node = QObject()
            node.setProperty("rel", rel)
            self._node_by_rel[rel] = node
            self._nodes.append(node)
        return node

    # --- path helpers ----------------------------------------------------------
    def _rel(self, abs_path: str) -> str:
        rel = os.path.relpath(abs_path, self._root)
        return rel.replace(os.sep, "/") if rel != "." else ""

    def _abs(self, rel: str) -> str:
        if not rel:
            return self._root
        return os.path.join(self._root, *rel.split("/"))

    def _children(self, rel: str) -> list[tuple[str, bool]]:
        names = self._tree.listdir(rel)
        out: list[tuple[str, bool]] = []
        for name in names:
            child_rel = f"{rel}/{name}" if rel else name
            out.append((name, self._tree.isdir(child_rel)))
        # Directories first, then files; each group alphabetical (matches
        # QFileSystemModel's default sort order).
        out.sort(key=lambda item: (not item[1], item[0].lower()))
        return out

    def _rel_from_index(self, index: QModelIndex) -> str:
        node = index.internalPointer()
        if node is None:
            return ""
        return str(node.property("rel"))

    # --- QAbstractItemModel interface ------------------------------------------
    def index(self, row: int, column: int, parent: QModelIndex | None = None) -> QModelIndex:
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            parent_rel = ""
        else:
            parent_rel = self._rel_from_index(parent)

        children = self._children(parent_rel)
        if 0 <= row < len(children):
            name, _ = children[row]
            child_rel = f"{parent_rel}/{name}" if parent_rel else name
            return self.createIndex(row, column, self._node_for(child_rel))
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        rel = self._rel_from_index(index)
        if "/" not in rel:
            return QModelIndex()
        parent_rel = rel.rsplit("/", 1)[0]
        if "/" in parent_rel:
            grandparent_rel, name = parent_rel.rsplit("/", 1)
        else:
            grandparent_rel, name = "", parent_rel
        children = self._children(grandparent_rel)
        for i, (child_name, _) in enumerate(children):
            if child_name == name:
                return self.createIndex(i, 0, self._node_for(parent_rel))
        return QModelIndex()

    def sibling(self, index: QModelIndex, row: int, column: int) -> QModelIndex:
        if not index.isValid():
            return self.index(row, column)
        parent = self.parent(index)
        return self.index(row, column, parent)

    def childCount(self, parent: QModelIndex) -> int:
        if not parent.isValid():
            parent_rel = ""
        else:
            parent_rel = self._rel_from_index(parent)
        return len(self._children(parent_rel))

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        return self.childCount(parent)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        rel = self._rel_from_index(index)
        if not rel:
            return None
        name = rel.rsplit("/", 1)[-1]
        if role == Qt.DisplayRole:
            return name
        if role == PATH_ROLE:
            return self._abs(rel)
        if role == IS_DIR_ROLE:
            return self._tree.isdir(rel)
        if role == Qt.DecorationRole:
            return self._folder_icon if self._tree.isdir(rel) else self._file_icon
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    # --- QFileSystemModel-compatible API ---------------------------------------
    def filePath(self, index: QModelIndex) -> str:
        if not index.isValid():
            return self._root
        rel = self._rel_from_index(index)
        return self._abs(rel)

    def index_by_path(self, path: str) -> QModelIndex:
        """Return the QModelIndex for an absolute path (like QFileSystemModel.index(path)).

        A path equal to the model root maps to the model's root index
        (an invalid QModelIndex) — the top-level items are its children.
        """
        rel = self._rel(path)
        if not rel:
            return QModelIndex()
        parts = rel.split("/")
        result = QModelIndex()
        for part in parts:
            children = self._children(
                self._rel_from_index(result) if result.isValid() else ""
            )
            found = False
            for i, (name, _) in enumerate(children):
                if name == part:
                    parent_rel = self._rel_from_index(result) if result.isValid() else ""
                    child_rel = f"{parent_rel}/{part}" if parent_rel else part
                    result = self.createIndex(i, 0, self._node_for(child_rel))
                    found = True
                    break
            if not found:
                return QModelIndex()
        return result

    def setRootPath(self, path: str) -> None:
        pass

    # --- mutation notification -------------------------------------------------
    def reload_tree(self) -> None:
        """Call after the underlying MemoryTree has been modified."""
        self.beginResetModel()
        self.endResetModel()
