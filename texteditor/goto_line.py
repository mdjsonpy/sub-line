"""Go to line dialog (Ctrl+G)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor


class GotoLineDialog(QDialog):
    def __init__(self, total_lines: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Go to Line")
        self.setFixedWidth(280)
        self._total_lines = total_lines

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QLabel(f"Go to line (1-{total_lines}):", self)
        layout.addWidget(label)

        self._input = QLineEdit(self)
        self._input.setPlaceholderText("line number")
        self._input.setValidator(None)
        self._input.selectAll()
        self._input.returnPressed.connect(self._accept)
        layout.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Go", self)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._result = -1
        self._input.setFocus()

    def _accept(self) -> None:
        try:
            n = int(self._input.text().strip())
        except ValueError:
            self.reject()
            return
        if 1 <= n <= self._total_lines:
            self._result = n
            self.accept()
        else:
            self.reject()

    def line(self) -> int:
        return self._result

    def go(self, pane) -> None:
        n = self.line()
        if n < 1:
            return
        cursor = QTextCursor(pane.document().findBlockByNumber(n - 1))
        pane.setTextCursor(cursor)
        pane.centerCursor()
