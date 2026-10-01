"""Vault management: unlock/lock encrypted workspaces.

A :class:`Vault` wraps a single ``.vault`` file. While unlocked, the
workspace contents live **entirely in process RAM** as a :class:`MemoryTree`.
No decrypted bytes are ever written to disk. Locking re-encrypts the
in-memory contents back into the vault file (atomic write) and clears RAM.

No Qt imports; pure Python and fully unit-testable.
"""
from __future__ import annotations

import os

from . import crypto
from .workspace_fs import MemoryTree


class VaultError(Exception):
    """Raised when a vault operation fails (bad password, missing file, ...)."""


class Vault:
    """Unlock/lock/change-password lifecycle for one encrypted workspace.

    While unlocked, the decrypted contents are held in :attr:`tree`
    (a :class:`MemoryTree` in RAM). No temp directory is created.
    """

    def __init__(self, workspace_id: str, vault_path: str) -> None:
        self._ws_id = workspace_id
        self._vault_path = vault_path
        self._tree: MemoryTree | None = None
        self._password: str | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._tree is not None

    @property
    def tree(self) -> MemoryTree | None:
        """The in-memory contents (None while locked)."""
        return self._tree

    @property
    def vault_path(self) -> str:
        return self._vault_path

    def unlock(self, password: str) -> MemoryTree:
        """Decrypt the vault into memory and return the :class:`MemoryTree`.

        Raises :class:`VaultError` on wrong password, missing vault file,
        or corrupt data.
        """
        if self.is_unlocked:
            return self._tree  # type: ignore[return-value]
        try:
            data = self._read_vault()
            tar = crypto.decrypt_vault(data, password)
        except (OSError, crypto.CryptoError) as exc:
            raise VaultError(str(exc)) from exc
        try:
            self._tree = MemoryTree.unpack_from_tar(tar)
        except Exception as exc:
            self._tree = None
            raise VaultError(f"failed to unpack vault: {exc}") from exc
        self._password = password
        return self._tree

    def lock(self) -> None:
        """Re-encrypt the in-memory contents into the vault file, then clear RAM.

        Raises :class:`VaultError` if the vault is not unlocked.
        """
        if not self.is_unlocked or self._password is None or self._tree is None:
            raise VaultError("vault is not unlocked")
        try:
            tar = self._tree.pack_to_tar()
            data = crypto.encrypt_vault(tar, self._password)
        except crypto.CryptoError as exc:
            raise VaultError(f"failed to pack/encrypt: {exc}") from exc
        try:
            self._atomic_write(data)
        except OSError as exc:
            raise VaultError(f"failed to write vault file: {exc}") from exc
        self._tree = None
        self._password = None

    def change_password(self, old_password: str, new_password: str) -> None:
        """Verify ``old_password`` and re-encrypt the vault with ``new_password``.

        Works while locked or unlocked. When unlocked, the live (possibly
        modified) in-memory contents are repacked before re-encryption.
        """
        if self.is_unlocked and self._tree is not None:
            # re-encrypt from live in-memory contents
            try:
                tar = self._tree.pack_to_tar()
            except Exception as exc:
                raise VaultError(f"failed to pack: {exc}") from exc
            self._password = new_password
        else:
            # locked: verify old password by decrypting, then re-encrypt
            try:
                data = self._read_vault()
                tar = crypto.decrypt_vault(data, old_password)
            except (OSError, crypto.CryptoError) as exc:
                raise VaultError("old password incorrect") from exc
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
    """Registry of :class:`Vault` instances."""

    def __init__(self) -> None:
        self._vaults: dict[str, Vault] = {}

    def get(self, workspace_id: str, vault_path: str) -> Vault:
        """Return the :class:`Vault` for ``workspace_id`` (creating it if new)."""
        v = self._vaults.get(workspace_id)
        if v is None or v.vault_path != vault_path:
            v = Vault(workspace_id, vault_path)
            self._vaults[workspace_id] = v
        return v

    def lock_all(self) -> None:
        """Lock every unlocked vault in the registry."""
        for v in list(self._vaults.values()):
            if v.is_unlocked:
                v.lock()

    def lock_one(self, workspace_id: str) -> None:
        """Lock a single vault by workspace id.

        Raises :class:`KeyError` if the id is not in the registry.
        """
        v = self._vaults[workspace_id]
        if v.is_unlocked:
            v.lock()

    def discard(self, workspace_id: str) -> None:
        """Remove a vault from the registry (in-memory only, nothing on disk)."""
        self._vaults.pop(workspace_id, None)
