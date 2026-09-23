"""Dialog for creating a new workspace, with optional encryption.

The dialog lets the user pick a root folder and optionally encrypt the
workspace with a password. It exposes a blocking static helper
(:meth:`NewWorkspaceDialog.get`) that returns
``(folder, encrypt, password)`` on accept, or ``None`` on cancel. When
encryption is enabled, the two password fields must match (checked in
:meth:`accept`, so every accept path is validated).
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QFileDialog,
    QDialogButtonBox,
)


class NewWorkspaceDialog(QDialog):
    """Pick a folder and (optionally) a password to encrypt the workspace."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Workspace")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Root folder:"))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Select a directory…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        self.encrypt = QCheckBox("Encrypt this workspace (requires a password to open)")
        self.encrypt.toggled.connect(self._on_toggled)
        layout.addWidget(self.encrypt)

        layout.addWidget(QLabel("Password:"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password)
        layout.addWidget(QLabel("Confirm password:"))
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.confirm)
        self._on_toggled(False)  # start disabled

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.folder.setFocus()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Workspace Folder")
        if folder:
            self.folder.setText(folder)

    def _on_toggled(self, on: bool) -> None:
        self.password.setEnabled(on)
        self.confirm.setEnabled(on)

    def accept(self) -> None:
        """Accept only when a folder is set and (if encrypting) the two
        password fields match; otherwise reject."""
        if not self.folder.text().strip():
            self.reject()
            return
        if self.encrypt.isChecked() and self.password.text() != self.confirm.text():
            self.reject()
            return
        super().accept()

    @staticmethod
    def get(parent) -> tuple[str, bool, str] | None:
        """Show the dialog; return ``(folder, encrypt, password)`` on OK,
        ``None`` on Cancel (or a validation failure)."""
        dlg = NewWorkspaceDialog(parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return (dlg.folder.text(), dlg.encrypt.isChecked(), dlg.password.text())
