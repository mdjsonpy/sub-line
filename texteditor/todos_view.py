"""Todos board: categories on the left, todo/doing/done columns on the right.

Holds a :class:`~texteditor.todos.Todos` model and exposes a non-interactive
API (add/rename/delete category, add/move/delete/re-prioritize task,
load/save) that both the widget UI and the tests drive.  Tasks can also be
dragged between the three columns of the selected category.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from .todos import (
    BUCKETS,
    BUCKET_LABELS,
    DEFAULT_PRIORITY,
    PRIORITY_META,
    PRIORITIES,
    TodoError,
    Todos,
    TodosStore,
)

TODO_MIME = "application/x-todos-task"


def _find_view(widget: QWidget | None) -> "TodosView | None":
    """Walk the Qt parent chain up to the owning TodosView.

    Resolving the view lazily (instead of storing a Python back-reference)
    avoids QObject reference cycles that can segfault at GC time.
    """
    w = widget
    while w is not None:
        if isinstance(w, TodosView):
            return w
        w = w.parent()
    return None


class TaskFormDialog(QDialog):
    """Modal form to enter a task's text and priority (used for New and Edit)."""

    def __init__(self, parent: QWidget | None, title: str = "New Task",
                 text: str = "", priority: int = DEFAULT_PRIORITY) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Task text…")
        self.text_edit.setText(text)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems([PRIORITY_META[p][0] for p in PRIORITIES])
        index = PRIORITIES.index(priority) if priority in PRIORITIES \
            else PRIORITIES.index(DEFAULT_PRIORITY)
        self.priority_combo.setCurrentIndex(index)

        form = QFormLayout()
        form.addRow("Task", self.text_edit)
        form.addRow("Priority", self.priority_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        box = QVBoxLayout(self)
        box.addLayout(form)
        box.addWidget(buttons)

        self.text_edit.selectAll()
        self.text_edit.setFocus()

    def result_text(self) -> str:
        return self.text_edit.text().strip()

    def result_priority(self) -> int:
        return PRIORITIES[self.priority_combo.currentIndex()]


class TodoBucket(QListWidget):
    """One todo/doing/done column; drag source and drop target for tasks."""

    def __init__(self, category: str, bucket: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = category
        self.bucket = bucket
        self.setWordWrap(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)


    # -- drag & drop ------------------------------------------------------------
    def mimeTypes(self) -> list[str]:
        return [TODO_MIME]

    def mimeData(self, indexes):  # QListWidget override, called on drag start
        rows = []
        for i in indexes or []:
            try:
                rows.append(i.row())
            except AttributeError:
                pass
        rows = sorted({r for r in rows if 0 <= r < self.count()})
        if not rows:
            return None
        mime = QMimeData()
        mime.setData(TODO_MIME, self.make_payload(rows))
        return mime

    def make_payload(self, indices: list[int]) -> bytes:
        return json.dumps(
            {"cat": self.category, "bucket": self.bucket, "indices": list(indices)}
        ).encode("utf-8")

    @staticmethod
    def parse_payload(mime: QMimeData) -> dict | None:
        if mime is None or not mime.hasFormat(TODO_MIME):
            return None
        try:
            data = json.loads(bytes(mime.data(TODO_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("indices"), list):
            return None
        return data

    def drop_allowed(self, mime: QMimeData) -> bool:
        """True if a drop carrying *mime* would move tasks into this bucket."""
        data = self.parse_payload(mime)
        if data is None:
            return False
        return (
            data.get("cat") == self.category
            and data.get("bucket") in BUCKETS
            and data["bucket"] != self.bucket
        )

    def handle_drop(self, mime: QMimeData) -> bool:
        """Apply a drop (move tasks from the source bucket into this one)."""
        data = self.parse_payload(mime)
        if not self.drop_allowed(mime):
            return False
        view = _find_view(self)
        if view is None:
            return False
        indices = [i for i in data["indices"] if isinstance(i, int)]
        if not indices:
            return False
        return view.move_tasks(self.category, data["bucket"], indices, self.category, self.bucket)

    def dragEnterEvent(self, event) -> None:
        if self.drop_allowed(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self.drop_allowed(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if self.handle_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    # -- context menu ------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        menu = self.build_menu(pos)
        if menu is not None:
            menu.exec(QCursor.pos())

    def build_menu(self, pos) -> QMenu | None:
        view = _find_view(self)
        if view is None:
            return None
        menu = QMenu(self)
        new_act = menu.addAction("New Todo")
        new_act.triggered.connect(lambda: view.prompt_new_task(self.category, self.bucket))
        item = self.itemAt(pos)
        if item is not None:
            idx = self.row(item)
            priority = item.data(Qt.UserRole)
            if not isinstance(priority, int):
                priority = DEFAULT_PRIORITY
            text = item.text()
            menu.addSeparator()
            edit_act = menu.addAction("Edit Task…")
            edit_act.triggered.connect(
                lambda: view.prompt_edit_task(self.category, self.bucket, idx, text, priority)
            )
            del_act = menu.addAction("Delete Task")
            del_act.triggered.connect(lambda: view.delete_tasks(self.category, self.bucket, [idx]))
            move_menu = self._build_move_menu(view, idx)
            if move_menu is not None:
                menu.addMenu(move_menu)
        return menu

    def _build_move_menu(self, view, idx: int) -> QMenu | None:
        """'Move to' submenu: sibling buckets plus a submenu per other category."""
        categories = view.todos().categories()
        siblings = [b for b in BUCKETS if b != self.bucket]
        others = [c for c in categories if c != self.category]
        if not siblings and not others:
            return None
        move_menu = QMenu("Move to", self)
        for b in siblings:
            act = move_menu.addAction(BUCKET_LABELS[b])
            act.triggered.connect(
                lambda _checked=False, b=b: view.move_tasks(self.category, self.bucket, [idx], self.category, b)
            )
        if others:
            if siblings:
                move_menu.addSeparator()
            for c in others:
                sub = move_menu.addMenu(c)
                for b in BUCKETS:
                    act = sub.addAction(BUCKET_LABELS[b])
                    act.triggered.connect(
                        lambda _checked=False, c=c, b=b: view.move_tasks(self.category, self.bucket, [idx], c, b)
                    )
        return move_menu


class TodoColumn(QWidget):
    """A titled column holding one task list (tasks are added via right-click)."""

    def __init__(self, category: str, bucket: str) -> None:
        super().__init__()
        self.category = category
        self.bucket = bucket

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        self.header = QLabel()
        self.header.setStyleSheet("font-weight: bold; padding: 4px 6px;")

        self.list = TodoBucket(category, bucket)

        box.addWidget(self.header)
        box.addWidget(self.list, 1)
        self.refresh()

    def refresh(self) -> None:
        view = _find_view(self)
        if view is None:
            return
        tasks = view.todos().tasks(self.category, self.bucket)
        self.list.clear()
        for t in tasks:
            item = QListWidgetItem(t.text)
            item.setData(Qt.UserRole, t.priority)
            label, color = PRIORITY_META.get(t.priority, PRIORITY_META[DEFAULT_PRIORITY])
            item.setForeground(QColor(color))
            item.setToolTip(f"Priority: {label}")
            self.list.addItem(item)
        self.header.setText(f"{BUCKET_LABELS[self.bucket]} ({len(tasks)})")


class TodosView(QWidget):
    """Categories list + board; non-interactive API over the Todos model."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None, fs=None) -> None:
        super().__init__(parent)
        self._todos = Todos()
        self._store = TodosStore(fs=fs)
        self._selected: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- categories column
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        cat_header = QLabel("Categories")
        cat_header.setStyleSheet("font-weight: bold; padding: 6px 8px;")
        self._cat_list = QListWidget()
        self._cat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._cat_list.customContextMenuRequested.connect(self._on_cat_menu)
        self._cat_list.currentRowChanged.connect(self._on_category_row)
        ll.addWidget(cat_header)
        ll.addWidget(self._cat_list, 1)

        # ---- board
        self._placeholder = QLabel("No category selected.\nAdd one on the left.")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._board = QSplitter(Qt.Horizontal)
        self._board.setChildrenCollapsible(False)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._board)

        split.addWidget(left)
        split.addWidget(self._stack)
        split.setSizes([180, 520])
        outer.addWidget(split)

        self._rebuild()

    # -- non-interactive API -----------------------------------------------------
    def todos(self) -> Todos:
        return self._todos

    def set_todos(self, t: Todos) -> None:
        self._todos = t
        self._selected = None
        self._rebuild()
        self.changed.emit()

    def has_tasks(self) -> bool:
        return any(self._todos.task_total(c) > 0 for c in self._todos.categories())

    def _mutate(self, fn) -> bool:
        try:
            fn()
        except TodoError:
            return False
        self._rebuild()
        self.changed.emit()
        return True

    def add_category(self, name: str) -> bool:
        return self._mutate(lambda: self._todos.add_category(name))

    def rename_category(self, old: str, new: str) -> bool:
        return self._mutate(lambda: self._todos.rename_category(old, new))

    def delete_category(self, name: str) -> bool:
        if name not in self._todos.categories():
            return False
        return self._mutate(lambda: self._todos.delete_category(name))

    def add_task(self, cat: str, bucket: str, text: str, priority: int = DEFAULT_PRIORITY) -> bool:
        return self._mutate(lambda: self._todos.add_task(cat, bucket, text, priority))

    def move_tasks(self, src_cat: str, src_bucket: str, indices, dst_cat: str, dst_bucket: str) -> bool:
        indices = [i for i in (indices or []) if isinstance(i, int)]
        if not indices or src_cat == dst_cat and src_bucket == dst_bucket:
            return True
        return self._mutate(
            lambda: self._todos.move_tasks(src_cat, src_bucket, indices, dst_cat, dst_bucket)
        )

    def delete_tasks(self, cat: str, bucket: str, indices) -> bool:
        indices = [i for i in (indices or []) if isinstance(i, int)]
        if not indices:
            return True
        return self._mutate(lambda: self._todos.delete_tasks(cat, bucket, indices))

    def set_task_priority(self, cat: str, bucket: str, index: int, priority: int) -> bool:
        def fn() -> None:
            tasks = self._todos.tasks(cat, bucket)
            if not (0 <= index < len(tasks)):
                raise TodoError(str(index))
            self._todos.update_task(cat, bucket, index, tasks[index].text, priority)

        return self._mutate(fn)

    def edit_task(self, cat: str, bucket: str, index: int, text: str,
                  priority: int = DEFAULT_PRIORITY) -> bool:
        def fn() -> None:
            tasks = self._todos.tasks(cat, bucket)
            if not (0 <= index < len(tasks)):
                raise TodoError(str(index))
            self._todos.update_task(cat, bucket, index, text, priority)

        return self._mutate(fn)

    # -- interactive prompts ----------------------------------------------------
    def prompt_new_task(self, cat: str, bucket: str) -> None:
        """Open the task form and add the task on accept."""
        dlg = TaskFormDialog(self, "New Task")
        if dlg.exec() == QDialog.Accepted:
            text = dlg.result_text()
            if text:
                self.add_task(cat, bucket, text, dlg.result_priority())

    def prompt_edit_task(self, cat: str, bucket: str, index: int,
                         text: str, priority: int) -> None:
        """Open the task form pre-filled and apply edits on accept."""
        dlg = TaskFormDialog(self, "Edit Task", text=text, priority=priority)
        if dlg.exec() == QDialog.Accepted:
            self.edit_task(cat, bucket, index, dlg.result_text(), dlg.result_priority())

    # -- persistence ---------------------------------------------------------------
    def load_from_file(self, path: str) -> bool:
        try:
            self._todos = self._store.load(path)
        except (OSError, ValueError):
            return False
        self._selected = None
        self._rebuild()
        self.changed.emit()
        return True

    def save_to_file(self, path: str) -> bool:
        try:
            self._store.save(self._todos, path)
        except (OSError, ValueError, TypeError):
            return False
        return True

    # -- UI internals ----------------------------------------------------------------
    def _rebuild(self) -> None:
        cats = self._todos.categories()
        self._cat_list.blockSignals(True)
        self._cat_list.clear()
        for name in cats:
            self._cat_list.addItem(QListWidgetItem(name))
        self._cat_list.blockSignals(False)
        if self._selected not in cats:
            self._selected = cats[0] if cats else None
        self._select(self._selected)

    def _select(self, name: str | None) -> None:
        if name is None:
            self._stack.setCurrentWidget(self._placeholder)
            return
        row = -1
        for i in range(self._cat_list.count()):
            if self._cat_list.item(i).text() == name:
                row = i
                break
        self._cat_list.blockSignals(True)
        self._cat_list.setCurrentRow(row)
        self._cat_list.blockSignals(False)
        self._rebuild_board(name)
        self._stack.setCurrentWidget(self._board)

    def _rebuild_board(self, cat: str) -> None:
        while self._board.count():
            w = self._board.widget(0)
            w.setParent(None)
            w.deleteLater()
        for bucket in BUCKETS:
            col = TodoColumn(cat, bucket)
            self._board.addWidget(col)
            col.refresh()
        self._board.setSizes([200] * len(BUCKETS))

    def _on_category_row(self, row: int) -> None:
        if 0 <= row < self._cat_list.count():
            name = self._cat_list.item(row).text()
            if name != self._selected:
                self._selected = name
                self._rebuild_board(name)
                self._stack.setCurrentWidget(self._board)

    def _prompt_new_category(self) -> None:
        name, ok = QInputDialog.getText(self, "New Category", "Category name:")
        if not ok:
            return
        name = name.strip()
        if name:
            self.add_category(name)

    def _on_cat_menu(self, pos) -> None:
        menu = QMenu(self)
        new_act = menu.addAction("New Category")
        item = self._cat_list.itemAt(pos)
        name = item.text() if item is not None else ""
        rename_act = delete_act = None
        if item is not None:
            menu.addSeparator()
            rename_act = menu.addAction("Rename…")
            delete_act = menu.addAction("Delete")
        chosen = menu.exec(QCursor.pos())
        if chosen is new_act:
            self._prompt_new_category()
        elif rename_act is not None and chosen is rename_act:
            new, ok = QInputDialog.getText(self, "Rename Category", "New name:", text=name)
            if ok:
                new = new.strip()
                if new and new != name:
                    self.rename_category(name, new)
        elif delete_act is not None and chosen is delete_act:
            ret = QMessageBox.question(
                self,
                "Delete Category",
                f"Delete '{name}' and all of its tasks?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self.delete_category(name)
