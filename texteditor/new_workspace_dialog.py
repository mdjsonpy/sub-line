"""Dialog for creating a new workspace.

Adapts its UI based on the selected folder's contents:

- **Empty folder** – offer plain workspace or encrypt (new vault).
- **Folder with plaintext files** – offer plain workspace or encrypt
  existing files (with a confirmation prompt).
- **Folder with a vault** – open as encrypted workspace (asks for the
  existing vault's password).

The blocking static helper :meth:`NewWorkspaceDialog.get` returns
``(folder, mode, password)`` on accept, or ``None`` on cancel.

``mode`` is one of:

- ``"plain"`` – plain (unencrypted) workspace.
- ``"encrypt_new"`` – create a new encrypted vault.
- ``"encrypt_existing"`` – adopt an existing vault in the folder.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QDialogButtonBox,
    QRadioButton,
    QButtonGroup,
    QMessageBox,
)


def _folder_state(folder: str) -> str:
    """Return ``"empty"``, ``"files"``, or ``"vault"`` for a directory."""
    if not folder or not os.path.isdir(folder):
        return "unknown"
    vault = os.path.join(folder, ".subline.vault")
    if os.path.isfile(vault):
        return "vault"
    try:
        entries = os.listdir(folder)
    except OSError:
        return "unknown"
    # "empty" means no entries other than .subline.vault (already handled)
    if not entries:
        return "empty"
    return "files"


class NewWorkspaceDialog(QDialog):
    """Pick a folder; UI adapts to the folder's encryption state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Workspace")
        self.setMinimumWidth(480)
        self._state: str = "unknown"
        self._build()

    # -- UI construction --------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Root folder:"))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Select a directory…")
        self.folder.editingFinished.connect(self._detect)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        # Mode group (radio buttons)
        self._mode_group = QButtonGroup(self)
        self._mode_group.idClicked.connect(self._on_mode_changed)
        self._btn_plain = QRadioButton("Plain workspace")
        self._btn_encrypt = QRadioButton("Encrypt workspace")
        self._mode_group.addButton(self._btn_plain)
        self._mode_group.addButton(self._btn_encrypt)
        self._mode_box = QVBoxLayout()
        self._mode_box.addWidget(self._btn_plain)
        self._mode_box.addWidget(self._btn_encrypt)
        layout.addLayout(self._mode_box)

        # Info label (context-sensitive)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: gray;")
        self._info.hide()
        layout.addWidget(self._info)

        # Password section
        self._pw_box = QVBoxLayout()
        layout.addLayout(self._pw_box)
        layout_label = QLabel("Password:")
        self._pw_box.addWidget(layout_label)
        self._pw_label = layout_label

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self._pw_box.addWidget(self.password)

        self.confirm_label = QLabel("Confirm password:")
        self._pw_box.addWidget(self.confirm_label)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self._pw_box.addWidget(self.confirm)

        self._set_pw_visible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.folder.setFocus()

    # -- state detection ---------------------------------------------------------
    def _detect(self) -> None:
        folder = self.folder.text().strip()
        self._state = _folder_state(folder)
        self._update_ui()

    def _update_ui(self) -> None:
        state = self._state
        if state == "vault":
            self._btn_plain.setVisible(False)
            self._btn_encrypt.setVisible(True)
            self._btn_encrypt.setText("Open encrypted workspace")
            self._btn_encrypt.setChecked(True)
            self._info.setText(
                "This folder already contains an encrypted vault. "
                "Enter the vault password to open it."
            )
            self._info.show()
            self._pw_label.setText("Vault password:")
            self.confirm_label.hide()
            self.confirm.hide()
            self.password.setEnabled(True)
        elif state == "files":
            self._btn_plain.setVisible(True)
            self._btn_encrypt.setVisible(True)
            self._btn_encrypt.setText("Encrypt existing files")
            self._btn_plain.setChecked(True)
            self._info.setText(
                "This folder contains files. Encrypting will lock them "
                "behind a password."
            )
            self._info.show()
            self._pw_label.setText("Password:")
            self.confirm_label.show()
            self.confirm.show()
            self._set_pw_visible(False)
        elif state == "empty":
            self._btn_plain.setVisible(True)
            self._btn_encrypt.setVisible(True)
            self._btn_encrypt.setText("Encrypt workspace")
            self._btn_plain.setChecked(True)
            self._info.hide()
            self._pw_label.setText("Password:")
            self.confirm_label.show()
            self.confirm.show()
            self._set_pw_visible(False)
        else:  # unknown
            self._btn_plain.setVisible(True)
            self._btn_encrypt.setVisible(True)
            self._btn_encrypt.setText("Encrypt workspace")
            self._btn_plain.setChecked(True)
            self._info.hide()
            self._pw_label.setText("Password:")
            self.confirm_label.show()
            self.confirm.show()
            self._set_pw_visible(False)

    def _on_mode_changed(self) -> None:
        encrypt_selected = self._btn_encrypt.isChecked()
        if self._state == "vault":
            self.password.setEnabled(encrypt_selected)
        else:
            self._set_pw_visible(encrypt_selected)

    def _set_pw_visible(self, visible: bool) -> None:
        self.password.setEnabled(visible)
        self.confirm.setEnabled(visible)
        if not visible:
            self.password.clear()
            self.confirm.clear()

    # -- interaction --------------------------------------------------------------
    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Workspace Folder")
        if folder:
            self.folder.setText(folder)
            self._detect()

    def _on_accept(self) -> None:
        folder = self.folder.text().strip()
        if not folder:
            self.reject()
            return
        state = self._state
        encrypt_selected = self._btn_encrypt.isChecked()

        if state == "vault":
            if not self.password.text():
                self._show_error("Vault password is required.")
                return
            super().accept()
        elif encrypt_selected:
            pw = self.password.text()
            if not pw:
                self._show_error("Password is required for encryption.")
                return
            if pw != self.confirm.text():
                self._show_error("Passwords do not match.")
                return
            if state == "files":
                ret = QMessageBox.question(
                    self,
                    "Confirm encryption",
                    "This will encrypt ALL files in the selected folder. "
                    "You will need the password to access them.\n\nAre you sure?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return
            super().accept()
        else:
            super().accept()

    def _show_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Validation", msg)
        self.reject()

    # -- result -------------------------------------------------------------------
    def get_result(self) -> tuple[str, str, str]:
        """Return ``(folder, mode, password)`` after accept."""
        folder = self.folder.text().strip()
        state = self._state
        encrypt_selected = self._btn_encrypt.isChecked()
        if state == "vault":
            return (folder, "encrypt_existing", self.password.text())
        if encrypt_selected:
            return (folder, "encrypt_new", self.password.text())
        return (folder, "plain", "")

    @staticmethod
    def get(parent) -> tuple[str, str, str] | None:
        """Show the dialog; return ``(folder, mode, password)`` on OK,
        ``None`` on Cancel."""
        dlg = NewWorkspaceDialog(parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.get_result()
