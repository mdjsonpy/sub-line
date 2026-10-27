"""Keyboard shortcuts reference dialog (F1 / Ctrl+?)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import Qt


SHORTCUTS = [
    ("Ctrl + O", "Open file"),
    ("Ctrl + S", "Save file"),
    ("Ctrl + Shift + S", "Save as"),
    ("Ctrl + W", "Close tab"),
    ("Ctrl + T", "New tab"),
    ("Ctrl + P", "Quick Open (fuzzy find file)"),
    ("Ctrl + G", "Go to line"),
    ("Ctrl + F", "Find"),
    ("Ctrl + H", "Find & replace"),
    ("Ctrl + N", "New workspace"),
    ("Ctrl + ,", "Preferences"),
    ("Ctrl + +", "Zoom in"),
    ("Ctrl + -", "Zoom out"),
    ("Ctrl + 0", "Reset zoom"),
    ("Ctrl + Q", "Quit"),
    ("F1", "Show this help"),
    ("Ctrl + Shift + F", "Find in folder"),
    ("Ctrl + Shift + V", "Toggle show hidden files"),
    ("Alt + Right", "Open in new split"),
    ("Delete", "Delete selected file/folder"),
    ("F2", "Rename file/folder"),
    ("Ctrl + Shift + N", "New folder in the selected location"),
    ("Ctrl + C", "Copy (editor / file tree)"),
    ("Ctrl + X", "Cut (file tree)"),
    ("Ctrl + V", "Paste (editor / file tree)"),
    ("Ctrl + A", "Select all (editor / file tree)"),
    ("Ctrl + Z", "Undo"),
    ("Ctrl + Shift + Z", "Redo"),
    ("Ctrl + D", "Duplicate line"),
    ("Ctrl + /", "Toggle comment"),
    ("Ctrl + L", "Select current line"),
    ("Alt + Up/Down", "Move line up/down"),
    ("Left / Right", "Previous / next file in folder (image, video, audio)"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setFixedSize(420, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        table = QTableWidget(len(SHORTCUTS), 2, self)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)

        for row, (shortcut, action) in enumerate(SHORTCUTS):
            table.setItem(row, 0, QTableWidgetItem(shortcut))
            table.setItem(row, 1, QTableWidgetItem(action))

        layout.addWidget(table)
