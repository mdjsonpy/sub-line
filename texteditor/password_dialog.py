"""Password dialogs for vault unlock and password change.

Both dialogs expose a blocking static helper (``get_password`` / ``get``)
that returns the entered value(s) on accept, or ``None`` on cancel.
``ChangePasswordDialog`` validates that the two new-password fields match
inside :meth:`accept`, so every accept path (button, Enter key) is checked.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QDialogButtonBox,
)


class PasswordDialog(QDialog):
    """Single password input dialog."""

    def __init__(self, title: str, prompt: str = "Password:", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.field.setFocus()
        self.field.returnPressed.connect(self.accept)

    @staticmethod
    def get_password(parent, title: str, prompt: str = "Password:") -> str | None:
        """Show the dialog; return the password on OK, ``None`` on Cancel."""
        dlg = PasswordDialog(title, prompt, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.field.text()


class ChangePasswordDialog(QDialog):
    """Old + new + confirm password dialog."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        def make_field() -> QLineEdit:
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            return edit

        layout.addWidget(QLabel("Current password:"))
        self.old = make_field()
        layout.addWidget(self.old)
        layout.addWidget(QLabel("New password:"))
        self.new = make_field()
        layout.addWidget(self.new)
        layout.addWidget(QLabel("Confirm new password:"))
        self.confirm = make_field()
        layout.addWidget(self.confirm)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.old.setFocus()

    def accept(self) -> None:
        """Accept only when new and confirm match; otherwise reject."""
        if self.new.text() != self.confirm.text():
            self.reject()
            return
        super().accept()

    @staticmethod
    def get(parent, title: str) -> tuple[str, str] | None:
        """Show the dialog; return ``(old, new)`` on OK, ``None`` on
        Cancel or a new/confirm mismatch."""
        dlg = ChangePasswordDialog(title, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return (dlg.old.text(), dlg.new.text())
