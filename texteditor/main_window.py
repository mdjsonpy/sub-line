"""Main application window: menu bar, workspaces list, single editor area, persistence."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import uuid

from PySide6.QtWidgets import (
    QMainWindow,
    QSplitter,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QMenu,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, QTimer, QSize

from . import APP_DISPLAY_NAME, __version__
from .theme import Theme
from .workspace import WorkspaceView
from .workspace_list import WorkspaceList
from .store import Workspace, WorkspacesStore, LayoutEntry, StoreData
from .todos_pane import TodosPane
from . import crypto
from .vault import VaultManager, VaultError
from .vault_service import VaultService
from .new_workspace_dialog import NewWorkspaceDialog


def _panel_icon(name: str) -> QIcon:
    """Load a panel icon from the package ``icons/`` directory."""
    path = os.path.join(os.path.dirname(__file__), "icons", name + ".svg")
    return QIcon(path)


class MainWindow(QMainWindow):
    def __init__(self, theme: Theme, store: WorkspacesStore) -> None:
        super().__init__()
        self._theme = theme
        self._store = store
        self._vaults = VaultManager()
        self._workspaces: list[Workspace] = []
        self._by_id: dict[str, Workspace] = {}
        self._layout: list[LayoutEntry] = []
        self._collapsed: set[str] = set()
        self._current_index: int = -1
        self._actions: dict[str, QAction] = {}

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1100, 720)

        self._view = WorkspaceView(None, self._theme)
        self._ws_list = WorkspaceList()

        self._split = QSplitter(Qt.Horizontal)
        self._split.addWidget(self._ws_list)
        self._split.addWidget(self._view)
        self._split.setChildrenCollapsible(False)
        self._split.setSizes([200, 900])

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_activity_bar())
        root.addWidget(self._split)
        self.setCentralWidget(central)

        self._vault_service = VaultService(
            parent=self,
            view=self._view,
            get_vaults=lambda: self._vaults,
            get_workspaces=lambda: self._workspaces,
            get_current_index=lambda: self._current_index,
            index_of=self._index_of,
            refresh=self._refresh_list,
            schedule_save=self._schedule_save,
        )

        self._ws_list.activated.connect(self.activate_workspace)
        self._ws_list.closeRequested.connect(self.close_workspace)
        self._ws_list.newRequested.connect(self.new_workspace)
        self._ws_list.renameRequested.connect(self.rename_workspace)
        self._ws_list.openFolderRequested.connect(self.open_in_explorer)
        self._ws_list.openTerminalRequested.connect(self.open_terminal)
        self._ws_list.moveUpRequested.connect(self.move_workspace_up)
        self._ws_list.moveDownRequested.connect(self.move_workspace_down)
        self._ws_list.moveToCategoryRequested.connect(self.move_workspace_to_category)
        self._ws_list.newCategoryRequested.connect(self.new_category)
        self._ws_list.renameCategoryRequested.connect(self.rename_category)
        self._ws_list.deleteCategoryRequested.connect(self.delete_category)
        self._ws_list.categoryUpRequested.connect(self.move_category_up)
        self._ws_list.categoryDownRequested.connect(self.move_category_down)
        self._ws_list.unlockRequested.connect(self._vault_service.on_unlock)
        self._ws_list.lockRequested.connect(self._vault_service.on_lock)
        self._ws_list.changePasswordRequested.connect(self._vault_service.on_change_password)
        self._ws_list.encryptRequested.connect(self._vault_service.on_encrypt)
        self._ws_list.decryptRequested.connect(self._vault_service.on_decrypt)
        self._ws_list.addFilesToVaultRequested.connect(self._vault_service.on_add_files_to_vault)
        self._ws_list.newWorkspaceInCategoryRequested.connect(self.new_workspace_in_category)
        self._view.tree.toggleWorkspacesRequested.connect(self.toggle_workspaces)
        self._view.tree.revealInExplorerRequested.connect(self.reveal_in_explorer)
        self._view.tree.createWorkspaceRequested.connect(self.new_workspace_from_folder)
        self._view.tabsChanged.connect(self._schedule_save)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_state)

        self._build_menus()
        self._load_workspaces()
        self._recover_vaults()

    # --- action factory --------------------------------------------------------
    def _act(self, key: str, text: str, slot, shortcut: str | None = None, checkable: bool = False) -> QAction:
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.setCheckable(checkable)
        act.triggered.connect(slot)
        self.addAction(act)
        self._actions[key] = act
        return act

    def _build_menus(self) -> None:
        mb = self.menuBar()
        for builder in (
            self._build_file_menu, self._build_edit_menu, self._build_markdown_menu,
            self._build_view_menu, self._build_todos_menu, self._build_help_menu,
        ):
            mb.addMenu(builder())

    def _build_file_menu(self) -> QMenu:
        m = QMenu("File", self)
        self._act("new_file", "New File", self.new_file, "Ctrl+N")
        self._act("open_file", "Open…", self.open_file_dialog, "Ctrl+O")
        self._act("quick_open", "Quick Open…", self.quick_open, "Ctrl+P")
        self._act("new_ws", "New Workspace…", self.new_workspace)
        self._act("save", "Save", self.save, "Ctrl+S")
        self._act("save_all", "Save All", self.save_all, "Ctrl+Shift+S")
        self._act("close_tab", "Close Tab", self.close_tab, "Ctrl+W")
        self._act("close_ws", "Close Workspace", self.close_current_workspace)
        self._act("quit", "Quit", self.close, "Ctrl+Q")
        m.addAction(self._actions["new_file"])
        m.addAction(self._actions["open_file"])
        m.addAction(self._actions["quick_open"])
        m.addAction(self._actions["new_ws"])
        m.addSeparator()
        m.addAction(self._actions["save"])
        m.addAction(self._actions["save_all"])
        m.addAction(self._actions["close_tab"])
        m.addAction(self._actions["close_ws"])
        m.addSeparator()
        m.addAction(self._actions["quit"])
        return m

    def _build_edit_menu(self) -> QMenu:
        m = QMenu("Edit", self)
        self._act("undo", "Undo", self.undo, "Ctrl+Z")
        self._act("redo", "Redo", self.redo, "Ctrl+Shift+Z")
        self._act("cut", "Cut", self.cut, "Ctrl+X")
        self._act("copy", "Copy", self.copy, "Ctrl+C")
        self._act("paste", "Paste", self.paste, "Ctrl+V")
        self._act("select_all", "Select All", self.select_all, "Ctrl+A")
        self._act("find", "Find", self.show_find, "Ctrl+F")
        m.addAction(self._actions["undo"])
        m.addAction(self._actions["redo"])
        m.addSeparator()
        m.addAction(self._actions["cut"])
        m.addAction(self._actions["copy"])
        m.addAction(self._actions["paste"])
        m.addAction(self._actions["select_all"])
        m.addSeparator()
        m.addAction(self._actions["find"])
        return m

    def _build_markdown_menu(self) -> QMenu:
        m = QMenu("Markdown", self)
        self._act("format_table", "Format Table", self.format_table, "Ctrl+Shift+T")
        self._actions["format_table"].setToolTip("Align the table under the cursor into a clean grid")
        self._act("format_all_tables", "Format All Tables", self.format_all_tables, "Ctrl+Shift+A")
        self._actions["format_all_tables"].setToolTip("Align every table in the document")
        m.addAction(self._actions["format_table"])
        m.addAction(self._actions["format_all_tables"])
        return m

    def _build_view_menu(self) -> QMenu:
        m = QMenu("View", self)
        self._act("wrap", "Toggle Word Wrap", self.toggle_wrap, "Ctrl+Shift+V", checkable=True)
        self._actions["wrap"].setChecked(True)
        self._act("whitespace", "Toggle Whitespace", self.toggle_whitespace, checkable=True)
        self._act("hidden", "Show Hidden Files", self.toggle_hidden, checkable=True)
        self._act("zoom_in", "Zoom In", self.zoom_in, "Ctrl+=")
        self._act("zoom_out", "Zoom Out", self.zoom_out, "Ctrl+-")
        self._act("zoom_reset", "Reset Zoom", self.zoom_reset, "Ctrl+0")
        self._act("next_tab", "Next Tab", self.next_tab, "Ctrl+Tab")
        self._act("prev_tab", "Previous Tab", self.previous_tab, "Ctrl+Shift+Tab")
        self._act("end_split", "End Split View", self.end_split)
        self._act("sidebar", "Toggle Sidebar", self.toggle_sidebar, "Ctrl+B", checkable=True)
        self._actions["sidebar"].setChecked(True)
        m.addAction(self._actions["wrap"])
        m.addAction(self._actions["whitespace"])
        m.addAction(self._actions["hidden"])
        m.addSeparator()
        m.addAction(self._actions["zoom_in"])
        m.addAction(self._actions["zoom_out"])
        m.addAction(self._actions["zoom_reset"])
        m.addSeparator()
        m.addAction(self._actions["next_tab"])
        m.addAction(self._actions["prev_tab"])
        m.addAction(self._actions["end_split"])
        m.addSeparator()
        m.addAction(self._actions["sidebar"])
        return m

    def _build_todos_menu(self) -> QMenu:
        m = QMenu("Todos", self)
        self._act("todos_new", "New Todos…", self.todos_new)
        self._act("todos_load", "Load Todos…", self.todos_load)
        self._act("todos_save", "Save Todos", self.todos_save)
        self._act("todos_save_as", "Save Todos As…", self.todos_save_as)
        self._act("todos_show", "Show Todos", self.toggle_todos, "Ctrl+T", checkable=True)
        m.addAction(self._actions["todos_new"])
        m.addAction(self._actions["todos_load"])
        m.addSeparator()
        m.addAction(self._actions["todos_save"])
        m.addAction(self._actions["todos_save_as"])
        m.addSeparator()
        m.addAction(self._actions["todos_show"])
        return m

    def _build_help_menu(self) -> QMenu:
        m = QMenu("Help", self)
        self._act("about", "About", self._about)
        m.addAction(self._actions["about"])
        return m

    # --- workspace lifecycle ---------------------------------------------------
    def current_workspace(self) -> Workspace | None:
        if 0 <= self._current_index < len(self._workspaces):
            return self._workspaces[self._current_index]
        return None

    def _current_ws_id(self) -> str | None:
        if 0 <= self._current_index < len(self._workspaces):
            return self._workspaces[self._current_index].id
        return None

    def _index_of(self, ws_id: str) -> int:
        for i, ws in enumerate(self._workspaces):
            if ws.id == ws_id:
                return i
        return -1

    def _rebuild_by_id(self) -> None:
        self._by_id = {ws.id: ws for ws in self._workspaces}

    def _category_names(self) -> list[str]:
        return [e.ref for e in self._layout if e.kind == "category"]

    def _load_workspaces(self) -> None:
        data = self._store.load()
        self._workspaces = data.workspaces
        self._layout = data.layout
        self._collapsed = set(data.collapsed)
        self._rebuild_by_id()

        # restore global view preferences BEFORE loading panes so new panes
        # inherit them
        self._actions["wrap"].setChecked(bool(data.settings["wrap"]))
        self._view.set_wrap(bool(data.settings["wrap"]))
        self._actions["whitespace"].setChecked(bool(data.settings["show_whitespace"]))
        self._view.set_show_whitespace(bool(data.settings["show_whitespace"]))
        self._actions["hidden"].setChecked(bool(data.settings.get("show_hidden", False)))
        self._view.tree.set_show_hidden(bool(data.settings.get("show_hidden", False)))
        self._view.set_zoom(int(data.settings["zoom"]))
        self._actions["sidebar"].setChecked(bool(data.settings["sidebar"]))
        self._view.tree.setVisible(bool(data.settings["sidebar"]))
        self._act_tree.setChecked(bool(data.settings["sidebar"]))

        # restore the selected workspace (fall back to the first in the layout)
        index = self._index_of(data.active_workspace_id) if data.active_workspace_id else -1
        if index < 0:
            first_ws_id = next(
                (e.ref for e in self._layout if e.kind == "workspace" and e.ref in self._by_id),
                None,
            )
            index = self._index_of(first_ws_id) if first_ws_id else -1
        self._current_index = index
        if index >= 0:
            ws = self._workspaces[index]
            if ws.encrypted:
                ws.root = ""
                self._view.workspace = ws
                self._view.close_all_tabs()
            else:
                self._view_load(ws)

        # restore window size + main splitter ratio
        self.resize(int(data.window["width"]), int(data.window["height"]))
        sizes = data.window["main_split"]
        if isinstance(sizes, list) and len(sizes) == 2:
            self._split.setSizes([int(sizes[0]), int(sizes[1])])

        self._refresh_list()

    def _refresh_list(self) -> None:
        self._ws_list.set_model(self._layout, self._by_id, self._collapsed, self._current_ws_id())

    def activate_workspace(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0 or index == self._current_index:
            return
        target = self._workspaces[index]
        if target.encrypted and not self._ensure_unlocked(target):
            return
        if self._current_index >= 0:
            if not self._view.confirm_close_all():
                return
            self._view.capture_into(self._workspaces[self._current_index])
        self._current_index = index
        self._view_load(target)
        self._refresh_list()
        self._schedule_save()

    def new_workspace(self) -> None:
        result = NewWorkspaceDialog.get(self)
        if result is None:
            return
        folder, mode, password = result
        self.new_workspace_from_folder(folder, mode=mode, password=password)

    def new_workspace_from_folder(self, folder: str, mode: str = "plain", password: str = "") -> str | None:
        """Create a workspace rooted at ``folder``.

        ``mode`` is ``"plain"``, ``"encrypt_new"``, or ``"encrypt_existing"``.

        Returns the new workspace id on success, or ``None`` if the action was
        cancelled or failed."""
        folder = os.path.abspath(folder)
        if mode in ("encrypt_new", "encrypt_existing") and not password:
            return None
        if self._current_index >= 0:
            if not self._view.confirm_close_all():
                return None
            self._view.capture_into(self._workspaces[self._current_index])
        ws_id = uuid.uuid4().hex
        if mode == "encrypt_new":
            ws = Workspace(id=ws_id, name=None, root="", tabs=[], active=0,
                           encrypted=True, vault_path=os.path.join(folder, ".subline.vault"))
            try:
                crypto.create_encrypted_workspace(folder, password)
            except crypto.CryptoError as exc:
                QMessageBox.warning(self, "Encryption failed", str(exc))
                self._refresh_list()
                return None
            if not self._unlock_with_password(ws, password):
                self._vaults.discard(ws_id)
                self._refresh_list()
                return None
        elif mode == "encrypt_existing":
            vault_path = os.path.join(folder, ".subline.vault")
            ws = Workspace(id=ws_id, name=None, root="", tabs=[], active=0,
                           encrypted=True, vault_path=vault_path)
            if not self._unlock_with_password(ws, password):
                self._vaults.discard(ws_id)
                self._refresh_list()
                return None
        else:
            ws = Workspace(id=ws_id, name=None, root=folder, tabs=[], active=0)
        self._workspaces.append(ws)
        self._layout.append(LayoutEntry("workspace", ws.id))
        self._rebuild_by_id()
        self._current_index = len(self._workspaces) - 1
        self._view_load(ws)
        self._refresh_list()
        self._schedule_save()
        return ws_id

    # --- encryption (delegates to VaultService) --------------------------------
    def _vault_for(self, ws: Workspace):
        return self._vault_service.vault_for(ws)

    def _ensure_unlocked(self, ws: Workspace) -> bool:
        return self._vault_service.ensure_unlocked(ws)

    def _unlock_with_password(self, ws: Workspace, password: str) -> bool:
        return self._vault_service.unlock_with_password(ws, password)

    def _lock_workspace(self, ws: Workspace) -> bool:
        return self._vault_service.lock_workspace(ws)

    def _view_load(self, ws: Workspace) -> None:
        self._vault_service.view_load(ws)

    def _sync_tree_for_workspace(self, ws: Workspace) -> None:
        self._vault_service.sync_tree(ws)

    def _resolve_collision(self, rel_path: str) -> str:
        return self._vault_service.resolve_collision(rel_path)

    def _recover_vaults(self) -> None:
        self._vault_service.recover()

    def close_workspace(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        current_id = self._current_ws_id()
        closing_current = index == self._current_index
        if closing_current and not self._view.confirm_close_all():
            return
        ws = self._workspaces[index]
        if ws.encrypted:
            self._lock_workspace(ws)  # re-encrypt + wipe temp; vault file is kept
        self._workspaces.pop(index)
        self._layout = [e for e in self._layout if not (e.kind == "workspace" and e.ref == ws_id)]
        self._rebuild_by_id()
        if self._workspaces:
            if closing_current:
                self._current_index = min(index, len(self._workspaces) - 1)
                self._view_load(self._workspaces[self._current_index])
            else:
                self._current_index = self._index_of(current_id)
        else:
            self._current_index = -1
            self._view.close_all_tabs()
            self._view.workspace = None
            self._view.tree.clear_vault_root()
        self._refresh_list()
        self._schedule_save()

    def close_current_workspace(self) -> None:
        ws_id = self._current_ws_id()
        if ws_id is not None:
            self.close_workspace(ws_id)

    def rename_workspace(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._workspaces[index]
        current = ws.name or ""
        name, ok = QInputDialog.getText(self, "Rename Workspace", "Workspace name:", text=current)
        if not ok:
            return
        name = name.strip()
        ws.name = name if name else None
        self._refresh_list()
        self._schedule_save()

    # --- categories ------------------------------------------------------------
    def new_category(self) -> None:
        name, ok = QInputDialog.getText(self, "New Category", "Category name:")
        if not ok:
            return
        name = name.strip()
        if not name or name in self._category_names():
            return
        self._layout.append(LayoutEntry("category", name))
        self._refresh_list()
        self._schedule_save()

    def rename_category(self, old: str) -> None:
        names = self._category_names()
        if old not in names:
            return
        new, ok = QInputDialog.getText(self, "Rename Category", "Category name:", text=old)
        if not ok:
            return
        new = new.strip()
        if not new or (new in names and new != old):
            return
        for entry in self._layout:
            if entry.kind == "category" and entry.ref == old:
                entry.ref = new
        if old in self._collapsed:
            self._collapsed.discard(old)
            self._collapsed.add(new)
        self._refresh_list()
        self._schedule_save()

    def _category_members(self, name: str) -> list[str]:
        ids: list[str] = []
        inside = False
        for entry in self._layout:
            if entry.kind == "category":
                inside = entry.ref == name
                continue
            if inside and entry.kind == "workspace":
                ids.append(entry.ref)
        return ids

    def delete_category(self, name: str) -> None:
        if name not in self._category_names():
            return
        member_ids = set(self._category_members(name))
        ret = QMessageBox.question(
            self,
            "Delete category",
            f"Deleting '{name}' will CLOSE {len(member_ids)} workspace(s).\n"
            "Files are NOT deleted. Continue?",
        )
        if ret != QMessageBox.Yes:
            return
        current_id = self._current_ws_id()
        if current_id in member_ids and not self._view.confirm_close_all():
            return
        self._workspaces = [ws for ws in self._workspaces if ws.id not in member_ids]
        self._layout = [
            e for e in self._layout
            if not (e.kind == "category" and e.ref == name)
            and not (e.kind == "workspace" and e.ref in member_ids)
        ]
        self._collapsed.discard(name)
        self._rebuild_by_id()
        if current_id is not None and current_id in self._by_id:
            self._current_index = self._index_of(current_id)
        elif self._workspaces:
            self._current_index = 0
            self._view_load(self._workspaces[0])
        else:
            self._current_index = -1
            self._view.close_all_tabs()
            self._view.workspace = None
            self._view.tree.clear_vault_root()
        self._refresh_list()
        self._schedule_save()

    def move_workspace_up(self, ws_id: str) -> None:
        self._move_workspace(ws_id, -1)

    def move_workspace_down(self, ws_id: str) -> None:
        self._move_workspace(ws_id, +1)

    def _group_of(self, index: int) -> str | None:
        for i in range(index - 1, -1, -1):
            entry = self._layout[i]
            if entry.kind == "category":
                return entry.ref
        return None

    def _move_workspace(self, ws_id: str, step: int) -> None:
        for i, entry in enumerate(self._layout):
            if entry.kind == "workspace" and entry.ref == ws_id:
                j = i + step
                if (
                    0 <= j < len(self._layout)
                    and self._layout[j].kind == "workspace"
                    and self._group_of(i) == self._group_of(j)
                ):
                    self._layout[i], self._layout[j] = self._layout[j], self._layout[i]
                    self._refresh_list()
                    self._schedule_save()
                return

    def _place_workspace_in_category(self, ws_id: str, category: str) -> None:
        """Re-home ``ws_id`` so it sits inside the ``category`` block, appended
        at the end of that block."""
        layout = [e for e in self._layout if not (e.kind == "workspace" and e.ref == ws_id)]
        entry = LayoutEntry("workspace", ws_id)
        for i, e in enumerate(layout):
            if e.kind == "category" and e.ref == category:
                k = i + 1
                while k < len(layout) and layout[k].kind == "workspace":
                    k += 1
                layout.insert(k, entry)
                break
        self._layout = layout
        self._refresh_list()
        self._schedule_save()

    def move_workspace_to_category(self, ws_id: str) -> None:
        if self._index_of(ws_id) < 0:
            return
        names = self._category_names()
        choice, ok = QInputDialog.getItem(
            self, "Move to Category", "Category:", names + ["Uncategorized"], 0, False
        )
        if not ok:
            return
        if choice == "Uncategorized":
            layout = [e for e in self._layout if not (e.kind == "workspace" and e.ref == ws_id)]
            layout.insert(0, LayoutEntry("workspace", ws_id))
            self._layout = layout
            self._refresh_list()
            self._schedule_save()
        else:
            self._place_workspace_in_category(ws_id, choice)

    def new_workspace_in_category(self, category: str) -> None:
        """Create a new workspace (via the New Workspace dialog) and file it
        under ``category``."""
        if category not in self._category_names():
            return
        result = NewWorkspaceDialog.get(self)
        if result is None:
            return
        folder, mode, password = result
        ws_id = self.new_workspace_from_folder(folder, mode=mode, password=password)
        if ws_id is None:
            return
        self._place_workspace_in_category(ws_id, category)

    def move_category_up(self, name: str) -> None:
        self._move_category(name, -1)

    def move_category_down(self, name: str) -> None:
        self._move_category(name, +1)

    def _move_category(self, name: str, step: int) -> None:
        idx = next(
            (i for i, e in enumerate(self._layout) if e.kind == "category" and e.ref == name),
            None,
        )
        if idx is None:
            return
        header = None
        if step < 0:
            for i in range(idx - 1, -1, -1):
                if self._layout[i].kind == "category":
                    header = i
                    break
        else:
            for i in range(idx + 1, len(self._layout)):
                if self._layout[i].kind == "category":
                    header = i
                    break
        if header is None:
            return
        lo, hi = min(idx, header), max(idx, header)
        end = len(self._layout)
        for i in range(hi + 1, len(self._layout)):
            if self._layout[i].kind == "category":
                end = i
                break
        block_a = self._layout[lo:hi]
        block_b = self._layout[hi:end]
        self._layout = self._layout[:lo] + block_b + block_a + self._layout[end:]
        self._refresh_list()
        self._schedule_save()

    def _build_activity_bar(self) -> QWidget:
        """Vertical activity bar with toggle buttons for the two side panels."""
        bar = QWidget()
        bar.setFixedWidth(44)
        bar.setStyleSheet(
            "QWidget { background: #212121; }"
            "QPushButton { border: none; border-left: 2px solid transparent; "
            "background: transparent; }"
            "QPushButton:hover { background: #2a2a2a; }"
            "QPushButton:checked { background: #2a2a2a; border-left: 2px solid #094771; }"
        )
        box = QVBoxLayout(bar)
        box.setContentsMargins(0, 8, 0, 8)
        box.setSpacing(4)

        self._act_workspaces = self._activity_button("Workspaces", "workspaces")
        self._act_tree = self._activity_button("Tree", "tree")
        box.addWidget(self._act_workspaces)
        box.addWidget(self._act_tree)
        box.addStretch(1)

        # The window is not shown yet, so use isHidden() for the intended state.
        self._act_workspaces.setChecked(not self._ws_list.isHidden())
        self._act_tree.setChecked(not self._view.tree.isHidden())
        self._act_workspaces.clicked.connect(self.toggle_workspaces)
        self._act_tree.clicked.connect(self._toggle_tree_from_bar)
        return bar

    def _activity_button(self, label: str, icon_name: str) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(_panel_icon(icon_name))
        btn.setIconSize(QSize(24, 24))
        btn.setFixedSize(36, 36)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(f"Show or hide the {label} panel")
        btn.setAccessibleName(label)
        return btn

    def _toggle_tree_from_bar(self) -> None:
        self.toggle_sidebar(not self._view.tree.isVisible())

    def toggle_workspaces(self) -> None:
        visible = not self._ws_list.isVisible()
        self._ws_list.setVisible(visible)
        self._act_workspaces.setChecked(visible)

    # --- todos -------------------------------------------------------------------
    def _todos_panes(self) -> list:
        return [p for p in self._view.panes() if isinstance(p, TodosPane)]

    def _active_todos_pane(self):
        pane = self._view.active_pane()
        if isinstance(pane, TodosPane):
            return pane
        panes = self._todos_panes()
        return panes[-1] if panes else None

    def _focus_todos_tab(self) -> None:
        panes = self._todos_panes()
        if panes:
            self._view.focus_pane(panes[0])
        else:
            if self.current_workspace() is None:
                self.new_workspace_from_folder(os.getcwd())
            self._view.new_todos_pane()

    def toggle_todos(self, on: bool | None = None) -> None:
        """Open (or focus) a Todos tab in the active workspace."""
        self._focus_todos_tab()
        self._actions["todos_show"].setChecked(True)

    def todos_new(self) -> None:
        """Create a fresh, untitled Todos tab in the active workspace."""
        if self.current_workspace() is None:
            self.new_workspace_from_folder(os.getcwd())
        pane = self._view.new_todos_pane()
        self._view.focus_pane(pane)

    def todos_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Todos", os.getcwd(),
            "Todos files (*.todo *.json);;All files (*)"
        )
        if not path:
            return
        if self.current_workspace() is None:
            self.new_workspace_from_folder(os.path.dirname(path) or os.getcwd())
        pane = self._view.new_todos_pane()
        pane.load(path)
        self._schedule_save()

    def todos_save(self) -> None:
        pane = self._active_todos_pane()
        if pane is not None:
            self._view.save_pane(pane)

    def todos_save_as(self) -> None:
        pane = self._active_todos_pane()
        if pane is None:
            return
        start = pane.filepath or os.path.join(os.getcwd(), "todos.todo")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Todos As", start,
            "Todos files (*.todo);;All files (*)"
        )
        if path and pane.saveAs(path):
            self._schedule_save()

    # --- open in explorer / terminal -------------------------------------------
    def open_in_explorer(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def reveal_in_explorer(self, path: str) -> None:
        """Reveal ``path`` in the OS file explorer, selecting it where supported.

        Works for both files and folders (unlike ``open_in_explorer`` which is
        folder-only). Falls back to opening the parent directory on platforms
        whose file manager has no selection flag.
        """
        if not path or not os.path.exists(path):
            return
        path = os.path.abspath(path)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path, "explore")  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                target = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.Popen(["xdg-open", target],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def open_terminal(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(
                    ["cmd", "/c", "start", "cmd.exe", "/k", "cd", "/d", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except Exception:
            return
        candidates = [
            ["gnome-terminal", "--working-directory", path],
            ["konsole", "--working-directory", path],
            ["xfce4-terminal", "--working-directory", path],
            ["x-terminal-emulator", "--working-directory", path],
            ["xterm", "-e", "sh", "-c", "cd %s && exec sh" % shlex.quote(path)],
        ]
        for cmd in candidates:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return

    # --- file actions ----------------------------------------------------------
    def open_paths(self, paths: list[str]) -> None:
        for raw in paths:
            path = os.path.abspath(os.path.expanduser(raw))
            if not os.path.exists(path):
                continue
            if os.path.isdir(path):
                target = next((ws for ws in self._workspaces if ws.root == path), None)
                if target is not None:
                    self.activate_workspace(target.id)
                else:
                    self.new_workspace_from_folder(path)
            else:
                if self._current_index < 0:
                    self.new_workspace_from_folder(os.path.dirname(path))
                self._view.open_file(path)
        self._schedule_save()

    def new_file(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.new_untitled()
        self._schedule_save()

    def open_file_dialog(self) -> None:
        if self.current_workspace() is None:
            return
        start = self._view.current_root() or os.getcwd()
        path, _ = QFileDialog.getOpenFileName(self, "Open file", start, "All files (*)")
        if path:
            self._view.open_file(path)
            self._schedule_save()

    def quick_open(self) -> None:
        if self.current_workspace() is None:
            return
        root = self._view.current_root()
        if not root or not os.path.isdir(root):
            return
        from .quick_open import QuickOpenDialog
        dlg = QuickOpenDialog(root, self)
        dlg.fileSelected.connect(self._on_quick_open_selected)
        dlg.exec()

    def _on_quick_open_selected(self, path: str) -> None:
        self._view.open_file(path)
        self._schedule_save()

    def close_tab(self) -> None:
        if self.current_workspace() is None:
            return
        if self._view.tab_count():
            self._view.close_active_tab()
            self._schedule_save()

    def next_tab(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.cycle_tab(1)

    def previous_tab(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.cycle_tab(-1)

    def end_split(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.end_split()
        self._schedule_save()

    def save(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.save_active()
        self._schedule_save()

    def save_all(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.save_all()
        self._schedule_save()

    # --- editor actions (delegate to active pane) ------------------------------
    def _tree_focused(self) -> bool:
        """True when keyboard focus is on the file-explorer tree view."""
        tree = getattr(self._view, "tree", None)
        return bool(tree is not None and tree.has_tree_focus())

    def _delegate(self, method: str) -> None:
        if self.current_workspace() is None:
            return
        pane = self._view.active_pane()
        if pane is not None and hasattr(pane, method):
            getattr(pane, method)()

    def undo(self): self._delegate("undo")
    def redo(self): self._delegate("redo")

    def cut(self):
        if self._tree_focused():
            self._view.tree.cut_files()
        else:
            self._delegate("cut")

    def copy(self):
        if self._tree_focused():
            self._view.tree.copy_files()
        else:
            self._delegate("copy")

    def paste(self):
        if self._tree_focused():
            self._view.tree.paste_files()
        else:
            self._delegate("paste")

    def select_all(self):
        if self._tree_focused():
            self._view.tree.select_all_files()
        else:
            self._delegate("selectAll")

    def format_table(self): self._delegate("format_table")
    def format_all_tables(self): self._delegate("format_all_tables")

    def show_find(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.show_find_bar()

    # --- view actions ----------------------------------------------------------
    def toggle_wrap(self, on: bool) -> None:
        if self.current_workspace() is None:
            return
        self._view.set_wrap(on)
        self._schedule_save()

    def toggle_whitespace(self, on: bool) -> None:
        if self.current_workspace() is None:
            return
        self._view.set_show_whitespace(on)
        self._schedule_save()

    def toggle_hidden(self, on: bool) -> None:
        self._view.tree.set_show_hidden(on)
        self._schedule_save()

    def zoom_in(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.zoom_in()
        self._schedule_save()

    def zoom_out(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.zoom_out()
        self._schedule_save()

    def zoom_reset(self) -> None:
        if self.current_workspace() is None:
            return
        self._view.zoom_reset()
        self._schedule_save()

    def toggle_sidebar(self, checked: bool) -> None:
        self._view.tree.setVisible(checked)
        self._act_tree.setChecked(checked)
        self._schedule_save()

    # --- persistence -----------------------------------------------------------
    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _current_settings(self) -> dict:
        return {
            "wrap": self._actions["wrap"].isChecked(),
            "show_whitespace": self._actions["whitespace"].isChecked(),
            "show_hidden": self._actions["hidden"].isChecked(),
            "zoom": self._view.zoom_offset(),
            "sidebar": self._view.tree.isVisible(),
        }

    def _current_window(self) -> dict:
        sizes = self._split.sizes()
        return {
            "width": self.width(),
            "height": self.height(),
            "main_split": [int(s) for s in sizes] if len(sizes) == 2 else [200, 900],
        }

    def _save_state(self) -> None:
        # capture the live view into the current workspace so its tabs/active
        # tab are persisted even without switching away first
        if self._current_index >= 0:
            self._view.capture_into(self._workspaces[self._current_index])
        collapsed = sorted(self._ws_list.collapsed_categories())
        self._store.save(StoreData(
            workspaces=self._workspaces,
            layout=self._layout,
            collapsed=collapsed,
            active_workspace_id=self._current_ws_id(),
            settings=self._current_settings(),
            window=self._current_window(),
        ))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"<b>{APP_DISPLAY_NAME}</b> {__version__}<br/>"
            "A Sublime-like text editor built with Python and PySide6.",
        )

    # --- close -----------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._current_index >= 0:
            if not self._view.confirm_close_all():
                event.ignore()
                return
        for ws in self._workspaces:
            if ws.encrypted:
                self._lock_workspace(ws)
        self._save_state()
        event.accept()
