"""Vault management: unlock/lock encrypted workspaces.

A :class:`Vault` wraps a single ``.vault`` file. While unlocked, the
workspace contents live in a per-workspace temp directory under the
manager's base dir (default ``$XDG_DATA_HOME/subline/vaults`` or
``~/.local/share/subline/vaults``). Locking re-encrypts the current
contents back into the vault file (atomic write) and securely deletes
the temp dir.

No Qt imports; pure Python and fully unit-testable.
"""
from __future__ import annotations

import os

from . import crypto


class VaultError(Exception):
    """Raised when a vault operation fails (bad password, missing file, ...)."""


def default_base_dir() -> str:
    """``$XDG_DATA_HOME/subline/vaults`` (or ``~/.local/share/subline/vaults``)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "subline", "vaults")


class Vault:
    """Unlock/lock/change-password lifecycle for one encrypted workspace."""

    def __init__(
        self,
        workspace_id: str,
        vault_path: str,
        base_dir: str | None = None,
    ) -> None:
        self._ws_id = workspace_id
        self._vault_path = vault_path
        self._base_dir = base_dir or default_base_dir()
        self._temp_path: str | None = None
        self._password: str | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._temp_path is not None

    @property
    def temp_path(self) -> str | None:
        return self._temp_path

    @property
    def vault_path(self) -> str:
        return self._vault_path

    def _temp_dir(self) -> str:
        return os.path.join(self._base_dir, self._ws_id)

    def unlock(self, password: str) -> str:
        """Decrypt the vault into the temp dir and return the dir path.

        Raises :class:`VaultError` on wrong password, missing vault file,
        or corrupt data.
        """
        if self.is_unlocked:
            return self._temp_path  # type: ignore[return-value]
        try:
            data = self._read_vault()
            tar = crypto.decrypt_vault(data, password)
        except (OSError, crypto.CryptoError) as exc:
            raise VaultError(str(exc)) from exc
        dest = self._temp_dir()
        if os.path.isdir(dest):
            crypto.secure_delete_dir(dest)  # remove stale data from a crash
        os.makedirs(self._base_dir, exist_ok=True)
        try:
            crypto.unpack_directory(tar, dest)
        except Exception as exc:
            if os.path.isdir(dest):
                crypto.secure_delete_dir(dest)
            raise VaultError(f"failed to unpack vault: {exc}") from exc
        self._password = password
        self._temp_path = dest
        return dest

    def lock(self) -> None:
        """Re-encrypt the temp dir into the vault file, then securely wipe it.

        Raises :class:`VaultError` if the vault is not unlocked.
        """
        if not self.is_unlocked or self._password is None:
            raise VaultError("vault is not unlocked")
        try:
            tar = crypto.pack_directory(self._temp_path)
            data = crypto.encrypt_vault(tar, self._password)
        except (OSError, crypto.CryptoError) as exc:
            raise VaultError(f"failed to pack/encrypt: {exc}") from exc
        try:
            self._atomic_write(data)
        except OSError as exc:
            raise VaultError(f"failed to write vault file: {exc}") from exc
        crypto.secure_delete_dir(self._temp_path)
        self._password = None
        self._temp_path = None

    def change_password(self, old_password: str, new_password: str) -> None:
        """Verify ``old_password`` and re-encrypt the vault with ``new_password``.

        Works while locked or unlocked. When unlocked, the live (possibly
        modified) contents are repacked before re-encryption.
        """
        try:
            data = self._read_vault()
            tar = crypto.decrypt_vault(data, old_password)  # verifies old pw
        except (OSError, crypto.CryptoError) as exc:
            raise VaultError("old password incorrect") from exc
        if self.is_unlocked:
            try:
                tar = crypto.pack_directory(self._temp_path)
            except (OSError, crypto.CryptoError) as exc:
                raise VaultError(f"failed to pack: {exc}") from exc
            self._password = new_password
        try:
            self._atomic_write(crypto.encrypt_vault(tar, new_password))
        except OSError as exc:
            raise VaultError(f"failed to write vault file: {exc}") from exc

    def _read_vault(self) -> bytes:
        if not os.path.isfile(self._vault_path):
            raise VaultError(f"vault file not found: {self._vault_path}")
        with open(self._vault_path, "rb") as fh:
            return fh.read()

    def _atomic_write(self, data: bytes) -> None:
        parent = os.path.dirname(os.path.abspath(self._vault_path))
        os.makedirs(parent, exist_ok=True)
        tmp = self._vault_path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._vault_path)


class VaultManager:
    """Registry of :class:`Vault` instances sharing one base dir."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = base_dir or default_base_dir()
        self._vaults: dict[str, Vault] = {}

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def get(self, workspace_id: str, vault_path: str) -> Vault:
        """Return the :class:`Vault` for ``workspace_id`` (creating it if new)."""
        v = self._vaults.get(workspace_id)
        if v is None or v.vault_path != vault_path:
            v = Vault(workspace_id, vault_path, self._base_dir)
            self._vaults[workspace_id] = v
        return v

    def lock_all(self) -> None:
        """Lock every unlocked vault in the registry."""
        for v in list(self._vaults.values()):
            if v.is_unlocked:
                v.lock()

    def recover_from_workspaces(self, workspaces) -> list[str]:
        """Re-encrypt any orphaned temp dirs found on disk after a crash.

        ``workspaces`` is the loaded list of :class:`Workspace`; for each
        encrypted workspace whose temp dir still exists but has no unlocked
        vault in the registry, its contents are re-encrypted (with a
        recovery key) back into the vault file and the temp dir wiped.
        Returns the ids that were recovered.
        """
        recovered = []
        for ws in workspaces:
            if not getattr(ws, "encrypted", False):
                continue
            ws_id = getattr(ws, "id", "")
            vault_path = getattr(ws, "vault_path", None) or getattr(ws, "root", None)
            if not ws_id or not vault_path or not os.path.isfile(vault_path):
                continue
            tmp = os.path.join(self._base_dir, ws_id)
            if not os.path.isdir(tmp):
                continue
            v = self._vaults.get(ws_id)
            if v is not None and v.is_unlocked:
                continue  # currently unlocked — leave it alone
            self._recover(tmp, vault_path)
            recovered.append(ws_id)
        return recovered

    def discard_orphan(self, workspace_id: str) -> None:
        """Securely delete the orphaned temp dir for ``workspace_id``."""
        path = os.path.join(self._base_dir, workspace_id)
        if os.path.isdir(path):
            crypto.secure_delete_dir(path)

    def _recover(self, tmp_dir: str, vault_path: str) -> None:
        """Re-encrypt ``tmp_dir`` into ``vault_path`` and securely wipe it."""
        tar = crypto.pack_directory(tmp_dir)
        data = crypto.encrypt_vault(tar, "orphan-recovery")
        parent = os.path.dirname(os.path.abspath(vault_path))
        os.makedirs(parent, exist_ok=True)
        tmp = vault_path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, vault_path)
        crypto.secure_delete_dir(tmp_dir)
