"""A find / replace bar shown above the editor (Ctrl+F).

Highlights all matches in the pane and supports replace / replace-all,
with optional case sensitivity.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QTextEdit,
)
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor


class FindBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pane = None
        self._case_sensitive = False
        self._last_query = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(self._on_text_changed)
        self.find_edit.returnPressed.connect(self.find_next)

        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("Replace")
        self.replace_edit.setClearButtonEnabled(True)
        self.replace_edit.returnPressed.connect(self.replace_one)

        self.case_check = QCheckBox("Aa", self)
        self.case_check.setToolTip("Case sensitive")
        self.case_check.toggled.connect(self._on_case_toggled)

        prev_btn = QPushButton("◀", self)
        prev_btn.setFixedWidth(32)
        prev_btn.setToolTip("Previous (Shift+Enter)")
        prev_btn.clicked.connect(self.find_previous)

        next_btn = QPushButton("▶", self)
        next_btn.setFixedWidth(32)
        next_btn.setToolTip("Next (Enter)")
        next_btn.clicked.connect(self.find_next)

        rep_btn = QPushButton("Replace", self)
        rep_btn.clicked.connect(self.replace_one)

        rep_all_btn = QPushButton("Replace All", self)
        rep_all_btn.clicked.connect(self.replace_all)

        close_btn = QPushButton("✕", self)
        close_btn.setFixedWidth(28)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.hide)

        layout.addWidget(self.find_edit, stretch=3)
        layout.addWidget(self.replace_edit, stretch=3)
        layout.addWidget(self.case_check)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)
        layout.addWidget(rep_btn)
        layout.addWidget(rep_all_btn)
        layout.addWidget(close_btn)

        self.find_edit.installEventFilter(self)
        self.replace_edit.installEventFilter(self)

    def setPane(self, pane) -> None:  # noqa: N802
        self._pane = pane
        self._refresh_matches()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.hide()
            return True
        return super().eventFilter(obj, event)

    def _on_text_changed(self, _text: str) -> None:
        self._last_query = self.find_edit.text()
        self._refresh_matches()

    def _on_case_toggled(self, on: bool) -> None:
        self._case_sensitive = on
        self._refresh_matches()

    # --- matching --------------------------------------------------------------
    @staticmethod
    def _all_matches(doc, query: str, case_sensitive: bool) -> list:
        """Return every match as (start, end) offsets via plain string search."""
        text = doc.toPlainText()
        hay = text if case_sensitive else text.lower()
        needle = query if case_sensitive else query.lower()
        if not needle:
            return []
        out = []
        i = hay.find(needle)
        while i != -1:
            out.append((i, i + len(query)))
            i = hay.find(needle, i + 1)
        return out

    def _selection_for(self, doc, start: int, end: int) -> QTextCursor:
        c = QTextCursor(doc)
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        return c

    def _refresh_matches(self) -> None:
        pane = self._pane
        if pane is None:
            return
        query = self.find_edit.text()
        if not query:
            pane.setExtraSelections([])
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#3a3a1e"))
        doc = pane.document()
        selections = []
        for start, end in self._all_matches(doc, query, self._case_sensitive):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = self._selection_for(doc, start, end)
            sel.format = fmt
            selections.append(sel)
        pane.setExtraSelections(selections)

    # --- navigation ------------------------------------------------------------
    def find_next(self) -> None:
        self._search(True)

    def find_previous(self) -> None:
        self._search(False)

    def _search(self, forward: bool) -> None:
        pane = self._pane
        query = self.find_edit.text()
        if pane is None or not query:
            return
        doc = pane.document()
        matches = self._all_matches(doc, query, self._case_sensitive)
        if not matches:
            return
        cursor = pane.textCursor()
        if cursor.hasSelection():
            anchor = cursor.selectionEnd() if forward else cursor.selectionStart()
        else:
            anchor = cursor.position()
        if forward:
            target = next(((s, e) for s, e in matches if s > anchor), matches[0])
        else:
            prior = [m for m in matches if m[1] <= anchor]
            target = prior[-1] if prior else matches[-1]
        pane.setTextCursor(self._selection_for(doc, target[0], target[1]))
        pane.centerCursor()

    # --- replace ---------------------------------------------------------------
    def replace_one(self) -> None:
        pane = self._pane
        query = self.find_edit.text()
        if pane is None or not query:
            return
        replacement = self.replace_edit.text()
        cursor = pane.textCursor()
        if cursor.hasSelection():
            selected = cursor.selectedText()
            cmp = (selected == query) if self._case_sensitive else (selected.lower() == query.lower())
            if cmp:
                cursor.insertText(replacement)
                self.find_next()
                return
        self.find_next()

    def replace_all(self) -> None:
        pane = self._pane
        query = self.find_edit.text()
        if pane is None or not query:
            return
        replacement = self.replace_edit.text()
        doc = pane.document()
        matches = self._all_matches(doc, query, self._case_sensitive)
        for start, end in reversed(matches):
            c = self._selection_for(doc, start, end)
            c.insertText(replacement)
        self._refresh_matches()
