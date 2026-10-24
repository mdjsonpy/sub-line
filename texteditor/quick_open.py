"""Quick Open dialog: fuzzy file finder (Ctrl+P)."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics


def _fuzzy_match(query: str, path: str) -> bool:
    """Return True if *query* is a subsequence of *path* (case-insensitive)."""
    q = query.lower()
    p = path.lower()
    qi = 0
    for ch in p:
        if qi < len(q) and ch == q[qi]:
            qi += 1
    return qi == len(q)


def _fuzzy_score(query: str, path: str) -> int:
    """Lower is better. Returns a large number if no match."""
    q = query.lower()
    p = path.lower()
    qi = 0
    score = 0
    consecutive = 0
    for i, ch in enumerate(p):
        if qi < len(q) and ch == q[qi]:
            qi += 1
            consecutive += 1
            # bonus for consecutive matches
            score += 1 - consecutive * 0.1
            # bonus for matching at word boundaries
            if i == 0 or p[i - 1] in "./_ -":
                score -= 2
        else:
            consecutive = 0
    if qi != len(q):
        return 10_000
    # shorter paths score better
    return score + len(p) * 0.01


class QuickOpenDialog(QDialog):
    fileSelected = Signal(str)

    def __init__(self, root: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Open")
        self._root = root
        self._all_files: list[str] = []
        self._set_size_policy()
        self._build_ui()
        self._collect_files()

    def _set_size_policy(self) -> None:
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(450, 350)
        self.resize(520, 400)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Type to search files…")
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._accept)
        layout.addWidget(self._edit)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setKeyboardSearch(False)
        self._list.itemActivated.connect(lambda item: self._open_item(item))
        layout.addWidget(self._list)

        self._edit.setFocus()

    def _collect_files(self) -> None:
        """Walk the workspace root and collect all file relative paths."""
        root = self._root
        if not os.path.isdir(root):
            return
        for dirpath, dirnames, filenames in os.walk(root):
            # skip hidden dirs and common junk
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                self._all_files.append(rel)
        self._all_files.sort(key=str.lower)

    def _on_text_changed(self, text: str) -> None:
        self._list.clear()
        if not text:
            # show first 200 files by default
            for rel in self._all_files[:200]:
                item = QListWidgetItem(rel)
                item.setData(Qt.UserRole, os.path.join(self._root, rel))
                self._list.addItem(item)
            if self._list.count():
                self._list.setCurrentRow(0)
            return

        scored: list[tuple[int, str]] = []
        for rel in self._all_files:
            s = _fuzzy_score(text, rel)
            if s < 10_000:
                scored.append((s, rel))
        scored.sort(key=lambda x: x[0])
        for s, rel in scored[:300]:
            item = QListWidgetItem(rel)
            item.setData(Qt.UserRole, os.path.join(self._root, rel))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _open_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.fileSelected.emit(path)
            self.accept()

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._open_item(item)
        else:
            self.reject()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)
