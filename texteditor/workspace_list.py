"""Workspaces list panel: a left column of workspaces grouped under categories.

Workspaces and category headers live in a single ``QTreeWidget``. Category
headers are bold top-level rows; workspaces nested under a header are its
children. Uncategorized workspaces are top-level rows interleaved with the
headers. All signals are keyed by workspace id or category name (not by
positional index).
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QMenu,
    QApplication,
)
from PySide6.QtGui import QCursor, QFont, QColor
from PySide6.QtCore import Qt, Signal

from .store import LayoutEntry
from .crypto import folder_has_plaintext

ROLE_KIND = Qt.UserRole        # "category" | "workspace"
ROLE_REF = Qt.UserRole + 1     # category name, OR workspace id
ROLE_LABEL = Qt.UserRole + 2   # base label (workspaces)
ROLE_ROOT = Qt.UserRole + 3    # workspace root path
ROLE_ENCRYPTED = Qt.UserRole + 4  # bool: workspace is an encrypted vault
ROLE_VAULT_PATH = Qt.UserRole + 5  # vault file path (encrypted workspaces)


class WorkspaceList(QWidget):
    activated = Signal(str)                 # workspace id
    closeRequested = Signal(str)            # workspace id
    renameRequested = Signal(str)           # workspace id
    newRequested = Signal()
    openFolderRequested = Signal(str)       # path
    openTerminalRequested = Signal(str)     # path
    moveUpRequested = Signal(str)           # workspace id
    moveDownRequested = Signal(str)         # workspace id
    moveToCategoryRequested = Signal(str)   # workspace id
    newCategoryRequested = Signal()
    renameCategoryRequested = Signal(str)   # category name
    deleteCategoryRequested = Signal(str)   # category name
    categoryUpRequested = Signal(str)       # category name
    categoryDownRequested = Signal(str)     # category name
    unlockRequested = Signal(str)           # workspace id (encrypted)
    lockRequested = Signal(str)             # workspace id (encrypted)
    changePasswordRequested = Signal(str)   # workspace id (encrypted)
    encryptRequested = Signal(str)          # workspace id (plain)
    decryptRequested = Signal(str)          # workspace id (encrypted)
    addFilesToVaultRequested = Signal(str)  # workspace id (mixed)
    newWorkspaceInCategoryRequested = Signal(str)  # category name

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QLabel("Workspaces", self)
        self.header.setStyleSheet(
            "padding: 6px 10px; font-weight: bold; color: #999; background: #252526;"
        )
        layout.addWidget(self.header)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

        self.new_btn = QPushButton("New Workspace", self)
        self.new_btn.setStyleSheet("margin: 6px;")
        self.new_btn.clicked.connect(self.newRequested.emit)
        layout.addWidget(self.new_btn)

    # --- item helpers ----------------------------------------------------------
    @staticmethod
    def _bold_font() -> QFont:
        font = QFont()
        font.setBold(True)
        return font

    @staticmethod
    def _normal_font() -> QFont:
        font = QFont()
        font.setBold(False)
        return font

    def _make_category(self, parent, name: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [name])
        item.setData(0, ROLE_KIND, "category")
        item.setData(0, ROLE_REF, name)
        item.setFont(0, self._bold_font())
        return item

    def _make_workspace(self, parent, ws_id: str, label: str, root: str,
                        encrypted: bool = False,
                        vault_path: str | None = None) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, ROLE_KIND, "workspace")
        item.setData(0, ROLE_REF, ws_id)
        item.setData(0, ROLE_LABEL, label)
        item.setData(0, ROLE_ROOT, root or "")
        item.setData(0, ROLE_ENCRYPTED, bool(encrypted))
        item.setData(0, ROLE_VAULT_PATH, vault_path or "")
        item.setFont(0, self._normal_font())
        if encrypted:
            item.setForeground(0, QColor("#e06060"))
            item.setText(0, f"\U0001F512 {label}")
        if root:
            item.setToolTip(0, root)
        return item

    # --- population ------------------------------------------------------------
    def set_model(self, layout: list, workspaces_by_id: dict,
                  collapsed: set, active_id: str | None) -> None:
        self.tree.clear()
        collapsed = collapsed or set()
        current_category: QTreeWidgetItem | None = None
        for entry in layout:
            if entry.kind == "category":
                current_category = self._make_category(self.tree, entry.ref)
                current_category.setExpanded(entry.ref not in collapsed)
            elif entry.kind == "workspace":
                ws = workspaces_by_id.get(entry.ref)
                if ws is None:
                    continue
                label = ws.name or ws.root or "?"
                parent = current_category if current_category is not None else self.tree
                self._make_workspace(parent, entry.ref, label, ws.root or "",
                                     encrypted=bool(getattr(ws, "encrypted", False)),
                                     vault_path=getattr(ws, "vault_path", None))
        self.set_active(active_id)

    def set_active(self, active_id: str | None) -> None:
        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, ROLE_KIND) == "workspace":
                ws_id = item.data(0, ROLE_REF)
                base = item.data(0, ROLE_LABEL) or item.text(0)
                if item.data(0, ROLE_ENCRYPTED):
                    base = f"\U0001F512 {base}"
                if ws_id == active_id:
                    item.setText(0, f"✓ {base}")
                    item.setFont(0, self._bold_font())
                else:
                    item.setText(0, base)
                    item.setFont(0, self._normal_font())
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def layout(self) -> list:
        out: list = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, ROLE_KIND) == "category":
                out.append(LayoutEntry("category", item.data(0, ROLE_REF)))
                for j in range(item.childCount()):
                    child = item.child(j)
                    out.append(LayoutEntry("workspace", child.data(0, ROLE_REF)))
            else:
                out.append(LayoutEntry("workspace", item.data(0, ROLE_REF)))
        return out

    def collapsed_categories(self) -> set:
        out: set = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, ROLE_KIND) == "category" and not item.isExpanded():
                out.add(item.data(0, ROLE_REF))
        return out

    def category_names(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, ROLE_KIND) == "category":
                out.append(item.data(0, ROLE_REF))
        return out

    # --- interaction -----------------------------------------------------------
    def _on_item_clicked(self, item: QTreeWidgetItem) -> None:
        if item.data(0, ROLE_KIND) == "workspace":
            self.activated.emit(item.data(0, ROLE_REF))

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            self._empty_menu()
            return
        if item.data(0, ROLE_KIND) == "workspace":
            self._workspace_menu(item)
        else:
            self._category_menu(item)

    def _empty_menu(self) -> None:
        menu = QMenu(self)
        act_new_cat = menu.addAction("New Category…")
        chosen = menu.exec(QCursor.pos())
        if chosen is act_new_cat:
            self.newCategoryRequested.emit()

    def _is_mixed(self, item: QTreeWidgetItem) -> bool:
        """True if this encrypted workspace also has plaintext files in its
        folder (mixed state)."""
        vault = item.data(0, ROLE_VAULT_PATH) or ""
        if not vault:
            return False
        folder = os.path.dirname(vault)
        return folder_has_plaintext(folder)

    def _build_workspace_menu(self, item: QTreeWidgetItem):
        """Build (but do not show) the workspace context menu.

        Returns ``(menu, acts, ws_id, root)`` where ``acts`` maps a stable
        action name (``rename``, ``unlock``, ``lock``, ``chpw``, ``copy``,
        ``open``, ``terminal``, ``encrypt``, ``decrypt``, ``addfiles``,
        ``up``, ``down``, ``movecat``, ``close``) to its ``QAction``.
        Encrypted workspaces expose the vault actions and hide the
        file-path actions; plain workspaces do the reverse.
        """
        ws_id = item.data(0, ROLE_REF)
        root = item.data(0, ROLE_ROOT) or ""
        encrypted = bool(item.data(0, ROLE_ENCRYPTED))
        menu = QMenu(self)
        acts: dict = {}
        acts["rename"] = menu.addAction("Rename Workspace")
        if encrypted:
            menu.addSeparator()
            acts["unlock"] = menu.addAction("Unlock…")
            acts["lock"] = menu.addAction("Lock")
            acts["chpw"] = menu.addAction("Change Password…")
            menu.addSeparator()
            mixed = self._is_mixed(item)
            if mixed:
                acts["addfiles"] = menu.addAction("Encrypt unencrypted files…")
            acts["decrypt"] = menu.addAction("Decrypt…")
        else:
            if root:
                menu.addSeparator()
                acts["copy"] = menu.addAction("Copy Absolute Path")
                acts["open"] = menu.addAction("Open in File Explorer")
                acts["terminal"] = menu.addAction("Open Terminal Here")
            menu.addSeparator()
            acts["encrypt"] = menu.addAction("Encrypt…")
        menu.addSeparator()
        acts["up"] = menu.addAction("Move Up")
        acts["down"] = menu.addAction("Move Down")
        acts["movecat"] = menu.addAction("Move to Category…")
        acts["close"] = menu.addAction("Close Workspace")
        return menu, acts, ws_id, root

    def _dispatch_workspace(self, acts: dict, ws_id: str, root: str, chosen) -> None:
        """Emit the signal for whichever action the user picked (or no-op if
        ``chosen`` is ``None``)."""
        if chosen is None:
            return
        key = None
        for name, action in acts.items():
            if action is chosen or action.text() == chosen.text():
                key = name
                break
        if key is None:
            return
        if key == "rename":
            self.renameRequested.emit(ws_id)
        elif key == "unlock":
            self.unlockRequested.emit(ws_id)
        elif key == "lock":
            self.lockRequested.emit(ws_id)
        elif key == "chpw":
            self.changePasswordRequested.emit(ws_id)
        elif key == "copy":
            self.copy_path_to_clipboard(root)
        elif key == "open":
            self.openFolderRequested.emit(root)
        elif key == "terminal":
            self.openTerminalRequested.emit(root)
        elif key == "encrypt":
            self.encryptRequested.emit(ws_id)
        elif key == "decrypt":
            self.decryptRequested.emit(ws_id)
        elif key == "addfiles":
            self.addFilesToVaultRequested.emit(ws_id)
        elif key == "up":
            self.moveUpRequested.emit(ws_id)
        elif key == "down":
            self.moveDownRequested.emit(ws_id)
        elif key == "movecat":
            self.moveToCategoryRequested.emit(ws_id)
        elif key == "close":
            self.closeRequested.emit(ws_id)

    def _workspace_menu(self, item: QTreeWidgetItem) -> None:
        menu, acts, ws_id, root = self._build_workspace_menu(item)
        chosen = menu.exec(QCursor.pos())
        self._dispatch_workspace(acts, ws_id, root, chosen)

    def _category_menu(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, ROLE_REF)
        menu = QMenu(self)
        act_new = menu.addAction("New Workspace")
        menu.addSeparator()
        act_rename = menu.addAction("Rename Category")
        act_toggle = menu.addAction("Collapse" if item.isExpanded() else "Expand")
        act_up = menu.addAction("Move Up")
        act_down = menu.addAction("Move Down")
        act_delete = menu.addAction("Delete Category")
        chosen = menu.exec(QCursor.pos())
        if chosen is act_new:
            self.newWorkspaceInCategoryRequested.emit(name)
        elif chosen is act_rename:
            self.renameCategoryRequested.emit(name)
        elif chosen is act_toggle:
            item.setExpanded(not item.isExpanded())
        elif chosen is act_up:
            self.categoryUpRequested.emit(name)
        elif chosen is act_down:
            self.categoryDownRequested.emit(name)
        elif chosen is act_delete:
            self.deleteCategoryRequested.emit(name)

    def copy_path_to_clipboard(self, path: str) -> bool:
        if not path:
            return False
        QApplication.clipboard().setText(path)
        return True
