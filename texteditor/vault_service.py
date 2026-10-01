"""Workspace encryption orchestration, decoupled from the UI.

The main window decides *what* to do (menu/shortcut); this service performs
the vault/crypto work and reports the outcome. It talks to the UI only through
narrow hooks, so it can be unit-tested with fakes.
"""
from __future__ import annotations

import os
from typing import Callable

from PySide6.QtWidgets import QMessageBox

from . import crypto
from .vault import VaultManager, VaultError
from .store import Workspace
from .workspace_fs import WorkspaceFS
from .password_dialog import PasswordDialog, ChangePasswordDialog


class VaultService:
    def __init__(
        self,
        *,
        parent,
        view,
        get_vaults: Callable[[], VaultManager],
        get_workspaces: Callable[[], list[Workspace]],
        get_current_index: Callable[[], int],
        index_of: Callable[[str], int],
        refresh: Callable[[], None],
        schedule_save: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._view = view
        self._get_vaults = get_vaults
        self._get_workspaces = get_workspaces
        self._get_current_index = get_current_index
        self._index_of = index_of
        self._refresh = refresh
        self._schedule_save = schedule_save

    # --- core vault operations -------------------------------------------------
    def vault_for(self, ws: Workspace):
        vault_path = ws.vault_path or ws.root
        if not vault_path:
            return None
        return self._get_vaults().get(ws.id, vault_path)

    def ensure_unlocked(self, ws: Workspace) -> bool:
        vault = self.vault_for(ws)
        if vault is None:
            QMessageBox.warning(self._parent, "Missing vault",
                                "This encrypted workspace has no vault file.")
            return False
        folder = os.path.dirname(ws.vault_path) if ws.vault_path else ""
        mixed = bool(folder) and crypto.folder_has_plaintext(folder)
        if vault.is_unlocked:
            ws.root = folder if mixed else (ws.vault_path or "")
            ws.workspace_fs = WorkspaceFS.from_tree(vault.tree, ws.root) if vault.tree else None
            return True
        password = PasswordDialog.get_password(self._parent, "Unlock workspace", "Password:")
        if password is None:
            return False
        try:
            vault.unlock(password)
        except VaultError as exc:
            QMessageBox.warning(self._parent, "Unlock failed", str(exc))
            return False
        ws.root = folder if mixed else (ws.vault_path or "")
        ws.workspace_fs = WorkspaceFS.from_tree(vault.tree, ws.root) if vault.tree else None
        return True

    def unlock_with_password(self, ws: Workspace, password: str) -> bool:
        """Unlock a vault with an explicit password (no dialog).

        Used when the caller already holds the password (e.g. just created the
        vault, or the user typed it in the new-workspace dialog).
        """
        vault = self.vault_for(ws)
        if vault is None:
            QMessageBox.warning(self._parent, "Missing vault",
                                "This encrypted workspace has no vault file.")
            return False
        try:
            vault.unlock(password)
        except VaultError as exc:
            QMessageBox.warning(self._parent, "Unlock failed", str(exc))
            return False
        folder = os.path.dirname(ws.vault_path) if ws.vault_path else ""
        mixed = bool(folder) and crypto.folder_has_plaintext(folder)
        ws.root = folder if mixed else (ws.vault_path or "")
        ws.workspace_fs = WorkspaceFS.from_tree(vault.tree, ws.root) if vault.tree else None
        return True

    def lock_workspace(self, ws: Workspace) -> bool:
        vault = self.vault_for(ws)
        if vault is None:
            return True
        if not vault.is_unlocked:
            return True
        try:
            vault.lock()
        except VaultError as exc:
            QMessageBox.warning(self._parent, "Lock failed", str(exc))
            return False
        ws.root = ""
        ws.workspace_fs = None
        return True

    # --- event handlers ---------------------------------------------------------
    def on_unlock(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if not ws.encrypted:
            return
        if not self.ensure_unlocked(ws):
            return
        self._refresh()
        if index == self._get_current_index():
            self.view_load(ws)
        self._schedule_save()

    def on_lock(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if not ws.encrypted or not self.lock_workspace(ws):
            return
        if index == self._get_current_index():
            self._view.workspace = ws
            self._view.close_all_tabs()
        self.sync_tree(ws)
        self._refresh()
        self._schedule_save()

    def on_change_password(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if not ws.encrypted:
            return
        vault = self.vault_for(ws)
        if vault is None:
            return
        result = ChangePasswordDialog.get(self._parent, "Change workspace password")
        if result is None:
            return
        old, new = result
        if not new:
            return
        try:
            vault.change_password(old, new)
        except VaultError as exc:
            QMessageBox.warning(self._parent, "Change password failed", str(exc))
            return
        QMessageBox.information(self._parent, "Password changed",
                                "The workspace password was updated.")

    def on_encrypt(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if ws.encrypted:
            return
        folder = ws.root
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self._parent, "Encrypt failed",
                                "This workspace has no folder to encrypt.")
            return
        existing_vault = os.path.join(folder, ".subline.vault")
        if os.path.isfile(existing_vault):
            if crypto.folder_has_plaintext(folder):
                self._merge_into_existing_vault(ws, folder, index, existing_vault)
            else:
                ws.encrypted = True
                ws.vault_path = existing_vault
                ws.root = ""
                ws.tabs = []
                ws.active = 0
                ws.splits = []
                ws.split_actives = []
                if index == self._get_current_index():
                    self._view.workspace = ws
                self.sync_tree(ws)
                self._refresh()
                self._schedule_save()
            return
        if index == self._get_current_index():
            if not self._view.confirm_close_all():
                return
            self._view.close_all_tabs()
        password = PasswordDialog.get_password(self._parent, "Encrypt workspace", "New password:")
        if not password:
            return
        try:
            vault_path = crypto.create_encrypted_workspace(folder, password)
        except (crypto.CryptoError, OSError) as exc:
            QMessageBox.warning(self._parent, "Encrypt failed", str(exc))
            return
        ws.encrypted = True
        ws.vault_path = vault_path
        ws.root = ""
        ws.tabs = []
        ws.active = 0
        ws.splits = []
        ws.split_actives = []
        if index == self._get_current_index():
            self._view.workspace = ws
        self.sync_tree(ws)
        self._refresh()
        self._schedule_save()

    def _merge_into_existing_vault(self, ws: Workspace, folder: str, index: int, vault_path: str) -> None:
        """Merge plaintext files into an existing vault in the folder."""
        if index == self._get_current_index():
            if not self._view.confirm_close_all():
                return
            self._view.close_all_tabs()
        password = PasswordDialog.get_password(
            self._parent, "Encrypt unencrypted files",
            "Enter the existing vault password:")
        if password is None:
            return
        try:
            crypto.merge_into_vault(folder, password, self.resolve_collision)
        except (crypto.CryptoError, OSError) as exc:
            QMessageBox.warning(self._parent, "Encrypt failed", str(exc))
            return
        ws.encrypted = True
        ws.vault_path = vault_path
        ws.root = ""
        ws.tabs = []
        ws.active = 0
        ws.splits = []
        ws.split_actives = []
        if index == self._get_current_index():
            self._view.workspace = ws
        self.sync_tree(ws)
        self._refresh()
        self._schedule_save()

    def on_decrypt(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if not ws.encrypted or not ws.vault_path:
            return
        folder = os.path.dirname(ws.vault_path)
        if not os.path.isdir(folder):
            QMessageBox.warning(self._parent, "Decrypt failed",
                                "The workspace folder no longer exists.")
            return
        if index == self._get_current_index():
            if not self._view.confirm_close_all():
                return
        vault = self.vault_for(ws)
        if vault is not None and vault.is_unlocked and not self.lock_workspace(ws):
            return
        if index == self._get_current_index():
            self._view.close_all_tabs()
        password = PasswordDialog.get_password(self._parent, "Decrypt workspace", "Password:")
        if password is None:
            return
        try:
            crypto.decrypt_workspace_inplace(folder, password)
        except (crypto.CryptoError, OSError) as exc:
            QMessageBox.warning(self._parent, "Decrypt failed", str(exc))
            return
        ws.encrypted = False
        ws.vault_path = None
        ws.root = folder
        ws.tabs = []
        ws.active = 0
        ws.splits = []
        ws.split_actives = []
        if index == self._get_current_index():
            self.view_load(ws)
        self.sync_tree(ws)
        self._refresh()
        self._schedule_save()

    def on_add_files_to_vault(self, ws_id: str) -> None:
        index = self._index_of(ws_id)
        if index < 0:
            return
        ws = self._get_workspaces()[index]
        if not ws.encrypted or not ws.vault_path:
            return
        folder = os.path.dirname(ws.vault_path)
        if not os.path.isdir(folder) or not crypto.folder_has_plaintext(folder):
            return
        if index == self._get_current_index():
            if not self._view.confirm_close_all():
                return
        vault = self.vault_for(ws)
        if vault is not None and vault.is_unlocked and not self.lock_workspace(ws):
            return
        if index == self._get_current_index():
            self._view.close_all_tabs()
        password = PasswordDialog.get_password(self._parent, "Encrypt unencrypted files", "Password:")
        if password is None:
            return
        try:
            crypto.merge_into_vault(folder, password, self.resolve_collision)
        except (crypto.CryptoError, OSError) as exc:
            QMessageBox.warning(self._parent, "Encrypt failed", str(exc))
            return
        ws.root = ""
        ws.tabs = []
        ws.active = 0
        ws.splits = []
        ws.split_actives = []
        if index == self._get_current_index():
            self.view_load(ws)
        self.sync_tree(ws)
        self._refresh()
        self._schedule_save()

    # --- utilities ---------------------------------------------------------------
    def recover(self) -> None:
        """Recover after a crash: ensure all encrypted workspaces have empty roots.

        In the in-memory vault system, no temp dirs are left behind, so
        recovery simply clears stale root paths.
        """
        for ws in self._get_workspaces():
            if ws.encrypted:
                ws.root = ""
                ws.workspace_fs = None

    def resolve_collision(self, rel_path: str) -> str:
        box = QMessageBox(self._parent)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("File conflict")
        box.setText(f'"{rel_path}" exists in both the vault and the folder.')
        box.setInformativeText("How do you want to resolve this?")
        overwrite = box.addButton("Overwrite with plaintext", QMessageBox.AcceptRole)
        keep_vault = box.addButton("Keep vault version", QMessageBox.RejectRole)
        keep_both = box.addButton("Keep both (rename)", QMessageBox.ActionRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            return "overwrite"
        if clicked is keep_vault:
            return "keep_vault"
        if clicked is keep_both:
            return "keep_both"
        return "cancelled"

    def sync_tree(self, ws: Workspace) -> None:
        if ws.encrypted and getattr(ws, "workspace_fs", None) is not None:
            self._view.tree.set_vault_root(ws.workspace_fs.root)
            return
        self._view.tree.clear_vault_root()

    def view_load(self, ws: Workspace) -> None:
        self._view.load_workspace(ws)
        self.sync_tree(ws)
