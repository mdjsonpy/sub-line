"""A workspace view: folder tree on the left, a stack of document tabs on the right.

Each workspace keeps its own independent tab stack (Sublime-like).
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QTabWidget,
    QTabBar,
    QMessageBox,
    QFileDialog,
    QMenu,
)
from PySide6.QtGui import QCursor
from PySide6.QtCore import Qt, Signal

from .theme import Theme
from .file_tree import FileTree
from .find_bar import FindBar
from .editor_view import EditorPane
from .binary_stub import BinaryPane
from .image_view import ImagePane, image_neighbor
from .todos_pane import TodosPane
from .binary import classify_file, KIND_TEXT, KIND_IMAGE
from .store import Workspace


class _MiddleClickCloseTabBar(QTabBar):
    """QTabBar that closes a tab when middle-clicked.

    Also reports right-clicks on a tab via ``tabContextMenuRequested(index)``.
    """

    tabContextMenuRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos) -> None:
        index = self.tabAt(pos)
        if index >= 0:
            self.tabContextMenuRequested.emit(index)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                return
        super().mousePressEvent(event)


class WorkspaceView(QWidget):
    activePaneChanged = Signal(object)
    tabsChanged = Signal()
    rootChanged = Signal()

    def __init__(self, workspace: Workspace | None, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._theme = theme
        self._wrap = True
        self._show_ws = False
        self._zoom = 0

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.tree = FileTree(self)
        self.tree.fileActivated.connect(self.open_file)
        self.tree.pathDeleted.connect(self._on_path_deleted)
        self.tree.openInGroupRequested.connect(self.open_in_side)

        # Right side: find bar (hidden) above the tab widget
        right = QWidget(self)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self.find_bar = FindBar(right)
        self.find_bar.hide()
        rv.addWidget(self.find_bar)

        self._active_group: QTabWidget | None = None
        self.tabs = self._make_tab_group()
        self._groups: list[QTabWidget] = [self.tabs]
        self._panes_splitter = QSplitter(Qt.Horizontal, right)
        self._panes_splitter.addWidget(self.tabs)
        self._panes_splitter.setChildrenCollapsible(False)
        self._active_group = self.tabs
        rv.addWidget(self._panes_splitter)

        self._main_splitter = QSplitter(Qt.Horizontal, self)
        self._main_splitter.addWidget(self.tree)
        self._main_splitter.addWidget(right)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setSizes([240, 800])

        outer.addWidget(self._main_splitter)

        if workspace is not None and workspace.root:
            self.set_root(workspace.root)

    # --- root / tree -----------------------------------------------------------
    def set_root(self, path: str) -> None:
        path = os.path.abspath(path)
        if self.workspace is not None:
            self.workspace.root = path
        self.tree.setRoot(path)

    def current_root(self) -> str | None:
        if self.workspace is not None:
            return self.workspace.root
        return self.tree.currentRoot()

    def load_workspace(self, ws: Workspace) -> None:
        self.workspace = ws
        if ws.root:
            self.set_root(ws.root)
        self.sync_from_data(ws)

    def capture_into(self, ws: Workspace) -> None:
        d = self.to_data()
        ws.tabs = d.tabs
        ws.active = d.active
        ws.splits = d.splits
        ws.split_actives = d.split_actives

    # --- split groups ----------------------------------------------------------
    def _make_tab_group(self) -> QTabWidget:
        group = QTabWidget(self)
        group.setMovable(True)
        group.setUsesScrollButtons(True)
        group.setElideMode(Qt.ElideRight)
        # NOTE: setTabsClosable must come AFTER setTabBar — in this PySide6
        # build, swapping the bar later resets tabsClosable and drops the
        # bar->widget tabCloseRequested signal forwarding.
        group.setTabBar(_MiddleClickCloseTabBar(group))
        group.setTabsClosable(True)
        group.tabCloseRequested.connect(lambda i, g=group: self.request_close_tab(i, g))
        group.tabBar().tabContextMenuRequested.connect(
            lambda i, g=group: self.show_tab_context_menu(g, i)
        )
        group.tabBar().tabMoved.connect(lambda _f, _t: self.tabsChanged.emit())
        group.currentChanged.connect(lambda _i, g=group: self._on_group_current_changed(g))
        return group

    def active_group(self) -> QTabWidget:
        if self._active_group is not None and self._active_group in self._groups:
            return self._active_group
        return self._groups[-1] if self._groups else self.tabs

    def _group_of(self, pane) -> QTabWidget | None:
        for g in self._groups:
            if g.indexOf(pane) >= 0:
                return g
        return None

    def _add_group_after(self, group: QTabWidget) -> QTabWidget:
        new_group = self._make_tab_group()
        i = self._groups.index(group)
        self._groups.insert(i + 1, new_group)
        self._panes_splitter.insertWidget(i + 1, new_group)
        return new_group

    def _add_group_at_end(self) -> QTabWidget:
        new_group = self._make_tab_group()
        self._groups.append(new_group)
        self._panes_splitter.addWidget(new_group)
        return new_group

    def _remove_group(self, group: QTabWidget) -> None:
        if len(self._groups) <= 1:
            return
        i = self._groups.index(group)
        self._groups.pop(i)
        group.setParent(None)        # removes the group from the QSplitter
        group.deleteLater()
        if self._active_group is group:
            self._active_group = self._groups[-1]
            self._on_group_current_changed(self._active_group)
        self.tabsChanged.emit()

    def left_group(self) -> QTabWidget:
        return self._groups[0]

    def right_group(self) -> QTabWidget:
        return self._groups[-1]

    def _ensure_right_group(self) -> QTabWidget:
        if len(self._groups) >= 2:
            return self._groups[-1]
        return self._add_group_after(self._groups[0])   # creates the one split

    def _on_group_current_changed(self, group: QTabWidget) -> None:
        self._active_group = group
        pane = group.currentWidget()
        if pane is not None and isinstance(pane, EditorPane):
            self.find_bar.setPane(pane)
        self.activePaneChanged.emit(pane)

    # --- tab management --------------------------------------------------------
    def _add_pane_to(self, group: QTabWidget, pane) -> None:
        pane.modificationChanged.connect(lambda m, p=pane: self._on_pane_modified(p, m))
        if isinstance(pane, EditorPane):
            pane.setWrap(self._wrap)
            pane.setShowWhitespace(self._show_ws)
            pane.set_zoom(self._zoom)
        if isinstance(pane, ImagePane):
            pane.navigateRequested.connect(lambda step, p=pane: self._navigate_image(p, step))
            pane.pathChanged.connect(lambda p=pane: self._update_pane_title(p))
        group.addTab(pane, pane.title)
        group.setCurrentWidget(pane)
        self._on_pane_modified(pane, pane.isModified())
        self.tabsChanged.emit()

    def _add_pane(self, pane) -> None:
        self._add_pane_to(self.active_group(), pane)

    def _make_pane_for(self, path: str):
        if path.lower().endswith(".todo"):
            pane = TodosPane()
            if pane.load(path):
                return pane
            pane.deleteLater()
        kind = classify_file(path)
        if kind == KIND_TEXT:
            pane = EditorPane(self._theme)
            pane.loadFile(path)
        elif kind == KIND_IMAGE:
            pane = ImagePane(path)
        else:
            pane = BinaryPane(path, kind)
        return pane

    def open_file(self, path: str):
        path = os.path.abspath(path)
        existing = self.pane_for_path(path)
        if existing is not None:
            group = self._group_of(existing)
            if group is not None:
                group.setCurrentWidget(existing)
            return existing

        pane = self._make_pane_for(path)
        self._add_pane(pane)
        # select in tree
        self.tree.selectPath(path)
        return pane

    def new_untitled(self):
        pane = EditorPane(self._theme)
        self._add_pane(pane)
        return pane

    def new_todos_pane(self) -> "TodosPane":
        pane = TodosPane()
        self._add_pane(pane)
        return pane

    def focus_pane(self, pane) -> None:
        group = self._group_of(pane)
        if group is not None:
            group.setCurrentWidget(pane)

    def pane_for_path(self, path: str):
        path = os.path.abspath(path)
        for pane in self.panes():
            fp = getattr(pane, "filepath", None)
            if fp and os.path.abspath(fp) == path:
                return pane
        return None

    def panes(self) -> list:
        out: list = []
        for g in self._groups:
            out.extend(g.widget(i) for i in range(g.count()))
        return out

    def active_pane(self):
        return self.active_group().currentWidget()

    def tab_count(self) -> int:
        return self.active_group().count()

    def request_close_tab(self, index: int, group: QTabWidget | None = None) -> None:
        group = group or self.active_group()
        if self._confirm_close(group, index):
            self._remove_tab(group, index)

    def close_active_tab(self) -> None:
        group = self.active_group()
        if group.count():
            self.request_close_tab(group.currentIndex(), group)

    def cycle_tab(self, step: int) -> None:
        """Move to the next/previous tab in the active group (wrapping)."""
        group = self.active_group()
        n = group.count()
        if n > 1:
            group.setCurrentIndex((group.currentIndex() + step) % n)

    def _remove_tab(self, group: QTabWidget, index: int) -> None:
        pane = group.widget(index)
        if pane is None:
            return
        group.removeTab(index)
        pane.deleteLater()
        self.tabsChanged.emit()
        if group.count() == 0:
            self._remove_group(group)
            return
        self._on_group_current_changed(group)

    def _on_path_deleted(self, path: str) -> None:
        """Close every tab whose file (or a file inside a folder) was deleted."""
        path = os.path.abspath(path)
        for group in list(self._groups):
            i = group.count() - 1
            while i >= 0:
                pane = group.widget(i)
                fp = getattr(pane, "filepath", None)
                if fp:
                    fp = os.path.abspath(fp)
                    if fp == path or fp.startswith(path + os.sep):
                        if self._confirm_close(group, i):
                            self._remove_tab(group, i)
                i -= 1

    # --- tab context menu ------------------------------------------------------
    def show_tab_context_menu(self, group: QTabWidget, index: int) -> None:
        menu = QMenu(self)
        act_move_left = menu.addAction("Move Tab to the Left")
        act_move_right = menu.addAction("Move Tab to the Right")
        act_end = menu.addAction("End Split View")
        menu.addSeparator()
        act_close = menu.addAction("Close Tab")
        menu.addSeparator()
        act_right = menu.addAction("Close to the Right")
        act_left = menu.addAction("Close to the Left")
        act_others = menu.addAction("Close Others")
        count = group.count()
        act_right.setEnabled(index + 1 < count)
        act_left.setEnabled(index > 0)
        act_others.setEnabled(count > 1)

        split = len(self._groups) >= 2
        left, right = self._groups[0], self._groups[-1]
        act_move_left.setEnabled(split and group is right)
        act_move_right.setEnabled((not split) or (split and group is left))
        act_end.setEnabled(split)

        chosen = menu.exec(QCursor.pos())
        if chosen is act_move_left:
            self.move_tab(group, index, "left")
        elif chosen is act_move_right:
            self.move_tab(group, index, "right")
        elif chosen is act_end:
            self.end_split()
        elif chosen is act_close:
            self.request_close_tab(index, group)
        elif chosen is act_right:
            self.close_tabs_to_right(group, index)
        elif chosen is act_left:
            self.close_tabs_to_left(group, index)
        elif chosen is act_others:
            self.close_others(group, index)

    # --- move tabs between panes (relocate, no clone) --------------------------
    def _move_pane(self, pane, target_group) -> None:
        src = self._group_of(pane)
        if src is None or src is target_group:
            return
        idx = src.indexOf(pane)
        if idx < 0:
            return
        src.removeTab(idx)                 # detach tab; widget stays alive
        target_group.addTab(pane, pane.title)   # re-parent + attach (append)
        target_group.setCurrentWidget(pane)
        self._on_pane_modified(pane, pane.isModified())  # refresh tab text
        self.tabsChanged.emit()
        if src.count() == 0 and len(self._groups) > 1:
            self._remove_group(src)        # auto-collapse an emptied pane

    def move_tab(self, group, index, side) -> None:
        pane = group.widget(index)
        if pane is None:
            return
        if side == "left":
            target = self._groups[0]
        else:
            target = self._ensure_right_group()
        if target is group:
            return
        self._move_pane(pane, target)

    def open_in_side(self, path: str, side: str) -> None:
        path = os.path.abspath(path)
        existing = self.pane_for_path(path)
        if existing is not None:
            g = self._group_of(existing)
            if g is not None:
                g.setCurrentWidget(existing)
            return
        target = self._groups[0] if side == "left" else self._ensure_right_group()
        pane = self._make_pane_for(path)
        self._add_pane_to(target, pane)
        self.tree.selectPath(path)

    def end_split(self) -> None:
        if len(self._groups) <= 1:
            return
        left = self._groups[0]
        right = self._groups[-1]
        while right.count():
            pane = right.widget(0)
            right.removeTab(0)             # detach (widget survives)
            left.addTab(pane, pane.title)  # re-parent into left
        cur = left.currentWidget()
        if cur is not None:
            left.setCurrentWidget(cur)
        self._remove_group(right)
        self.tabsChanged.emit()

    # --- image navigation ------------------------------------------------------
    def _navigate_image(self, pane, step) -> None:
        if not getattr(pane, "filepath", None):
            return
        neighbor = image_neighbor(pane.filepath, step)
        if neighbor:
            pane.set_path(neighbor)

    def _update_pane_title(self, pane) -> None:
        group = self._group_of(pane)
        if group is None:
            return
        i = group.indexOf(pane)
        if i < 0:
            return
        group.setTabText(i, pane.title + (" ●" if pane.isModified() else ""))

    def _close_indices(self, group: QTabWidget, indices: list[int]) -> bool:
        """Confirm every modified tab, then remove them. False if cancelled."""
        if not indices:
            return True
        for i in sorted(set(indices)):
            if not self._confirm_close(group, i):
                return False
        for i in sorted(set(indices), reverse=True):
            self._remove_tab(group, i)
        return True

    def close_tabs_to_right(self, group: QTabWidget, index: int) -> None:
        indices = list(range(index + 1, group.count()))
        if indices and self._close_indices(group, indices):
            group.setCurrentIndex(min(index, group.count() - 1))

    def close_tabs_to_left(self, group: QTabWidget, index: int) -> None:
        indices = list(range(index))
        if indices and self._close_indices(group, indices):
            group.setCurrentIndex(index)

    def close_others(self, group: QTabWidget, index: int) -> None:
        indices = [i for i in range(group.count()) if i != index]
        if indices and self._close_indices(group, indices):
            group.setCurrentIndex(index)

    def _on_pane_modified(self, pane, modified: bool) -> None:
        group = self._group_of(pane)
        if group is None:
            return
        index = group.indexOf(pane)
        if index < 0:
            return
        base = pane.title
        group.setTabText(index, base + (" ●" if modified else ""))

    # --- saving ----------------------------------------------------------------
    def save_pane(self, pane) -> bool:
        if pane is None:
            return False
        if getattr(pane, "filepath", None) is not None:
            return pane.save()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save As", os.path.expanduser("~"), "All files (*)"
        )
        if not path:
            return False
        return pane.saveAs(path)

    def save_active(self) -> bool:
        pane = self.active_pane()
        if pane is None:
            return False
        if pane.isModified():
            return self.save_pane(pane)
        return True

    def save_all(self) -> bool:
        ok = True
        for pane in self.panes():
            if pane.isModified():
                if not self.save_pane(pane):
                    ok = False
        return ok

    def _confirm_close(self, group: QTabWidget, index: int) -> bool:
        pane = group.widget(index)
        if pane is None:
            return True
        if not pane.isModified():
            return True
        title = pane.title
        ret = QMessageBox.question(
            self,
            "Unsaved changes",
            f"'{title}' has unsaved changes.\nSave before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if ret == QMessageBox.Save:
            if self.save_pane(pane):
                return True
            ret2 = QMessageBox.question(
                self,
                "Could not save",
                "Saving failed. Discard changes and close anyway?",
                QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Discard,
            )
            return ret2 == QMessageBox.Discard
        return ret == QMessageBox.Discard

    def confirm_close_all(self) -> bool:
        """Ask about every modified tab. Return True if all may be closed."""
        for g in self._groups:
            for i in range(g.count()):
                pane = g.widget(i)
                if pane is not None and pane.isModified():
                    if not self._confirm_close(g, i):
                        return False
        return True

    def close_all_tabs(self) -> None:
        for g in list(self._groups[1:]):
            self._remove_group(g)
        g = self._groups[0]
        while g.count():
            pane = g.widget(0)
            g.removeTab(0)
            pane.deleteLater()
        self.tabsChanged.emit()
        self._on_group_current_changed(g)

    # --- state <-> data --------------------------------------------------------
    def sync_from_data(self, ws: Workspace) -> None:
        self.close_all_tabs()
        splits = ws.splits or [list(ws.tabs)]
        if not splits:
            splits = [[]]
        if len(splits) > 2:
            right = splits[1]
            for extra in splits[2:]:
                right = right + extra
            splits = [splits[0], right]
        actives = ws.split_actives or []
        for si, tablist in enumerate(splits):
            group = self._groups[si] if si < len(self._groups) else self._add_group_at_end()
            for path in tablist:
                if os.path.isfile(path):
                    self._add_pane_to(group, self._make_pane_for(path))
                else:
                    # file gone: open a read-only stub so the tab still exists
                    self._add_pane_to(group, BinaryPane(path, "binary"))
        for si, g in enumerate(self._groups):
            if g.count():
                act = actives[si] if si < len(actives) else 0
                g.setCurrentIndex(max(0, min(act, g.count() - 1)))
        self._active_group = self._groups[0]
        for g in self._groups:
            if g.count():
                self._active_group = g
                break
        if self.workspace is not None:
            self.workspace.tabs = [p for split in splits for p in split]
            self.workspace.active = self._groups[0].currentIndex() if self._groups[0].count() else 0

    def to_data(self) -> Workspace:
        kept: list[tuple] = []
        for g in self._groups:
            tablist = [
                p.filepath
                for p in (g.widget(i) for i in range(g.count()))
                if getattr(p, "filepath", None)
            ]
            # drop panels with no restorable tab so a lone empty panel is not
            # persisted as a separate split group
            if tablist:
                kept.append((g, tablist))
        splits = [tablist for _g, tablist in kept]
        actives = [max(0, g.currentIndex()) for g, _tablist in kept]
        ws = Workspace(
            id=self.workspace.id if self.workspace else "",
            name=self.workspace.name if self.workspace else None,
            root=self.workspace.root if self.workspace else None,
            tabs=[p for split in splits for p in split],
            active=actives[0] if actives else 0,
            splits=splits,
            split_actives=actives,
        )
        return ws

    def show_find_bar(self) -> None:
        pane = self.active_pane()
        if isinstance(pane, EditorPane):
            self.find_bar.setPane(pane)
            self.find_bar.show()
            self.find_bar.find_edit.setFocus()
            self.find_bar.find_edit.selectAll()

    def hide_find_bar(self) -> None:
        self.find_bar.hide()

    def zoom_in(self) -> None:
        self._zoom = min(16, self._zoom + 1)
        self._zoom_all("zoomIn")

    def zoom_out(self) -> None:
        self._zoom = max(-8, self._zoom - 1)
        self._zoom_all("zoomOut")

    def zoom_reset(self) -> None:
        self._zoom = 0
        self._zoom_all("resetZoom")

    def set_zoom(self, offset: int) -> None:
        """Restore a persisted editor zoom offset (applies to text panes)."""
        self._zoom = max(-8, min(16, int(offset)))
        for pane in self.panes():
            if isinstance(pane, EditorPane):
                pane.set_zoom(self._zoom)

    def zoom_offset(self) -> int:
        return self._zoom

    def _zoom_all(self, method: str) -> None:
        for pane in self.panes():
            if isinstance(pane, (EditorPane, ImagePane)):
                getattr(pane, method)()

    def set_wrap(self, on: bool) -> None:
        self._wrap = on
        for pane in self.panes():
            if isinstance(pane, EditorPane):
                pane.setWrap(on)

    def set_show_whitespace(self, on: bool) -> None:
        self._show_ws = on
        for pane in self.panes():
            if isinstance(pane, EditorPane):
                pane.setShowWhitespace(on)

    def toggle_sidebar(self) -> None:
        if self._main_splitter is not None:
            visible = self.tree.isVisible()
            self.tree.setVisible(not visible)
