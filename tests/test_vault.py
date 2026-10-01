"""Tests for vault lifecycle: unlock/lock cycles, password changes,
manager registry — using the in-memory API (no temp dirs).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from texteditor.crypto import encrypt_vault, pack_directory
from texteditor.vault import Vault, VaultError, VaultManager


def make_vault_file(tmp_path, ws_id: str, password: str, files: dict) -> str:
    """Create a ``.vault`` file containing the given {relpath: content} files."""
    ws_dir = tmp_path / f"src-{ws_id}"
    for rel, content in files.items():
        p = ws_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    tar = pack_directory(str(ws_dir))
    vault_path = str(tmp_path / f"{ws_id}.vault")
    with open(vault_path, "wb") as fh:
        fh.write(encrypt_vault(tar, password))
    return vault_path


class TestUnlockLock:
    def test_unlock_lock_cycle(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "mypw", {"a.txt": "hello"})
        v = Vault("ws1", vault_path)
        assert not v.is_unlocked
        assert v.tree is None

        tree = v.unlock("mypw")
        assert v.is_unlocked
        assert v.tree is tree
        assert tree.read_bytes("a.txt").decode() == "hello"

        tree.write_bytes("b.txt", b"new file")

        v.lock()
        assert not v.is_unlocked
        assert v.tree is None

        # Re-unlock: both original and modified files must be present
        tree2 = v.unlock("mypw")
        assert tree2.read_bytes("a.txt").decode() == "hello"
        assert tree2.read_bytes("b.txt").decode() == "new file"
        v.lock()

    def test_unlock_wrong_password(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "correct", {"a.txt": "x"})
        v = Vault("ws1", vault_path)
        with pytest.raises(VaultError):
            v.unlock("wrong")
        assert not v.is_unlocked

    def test_unlock_missing_vault_file(self, tmp_path):
        v = Vault("ws1", str(tmp_path / "nope.vault"))
        with pytest.raises(VaultError):
            v.unlock("pw")
        assert not v.is_unlocked

    def test_unlock_no_plaintext_on_disk(self, tmp_path):
        """After unlock, no new plaintext files should be created on disk."""
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "secret-data"})
        # Record all files that exist before unlock
        before = set()
        for dirpath, _dirs, files in os.walk(str(tmp_path)):
            for f in files:
                before.add(os.path.join(dirpath, f))
        v = Vault("ws1", vault_path)
        v.unlock("pw")
        # No new files should have been created
        after = set()
        for dirpath, _dirs, files in os.walk(str(tmp_path)):
            for f in files:
                after.add(os.path.join(dirpath, f))
        new_files = after - before
        assert not new_files, f"plaintext leaked: {new_files}"
        v.lock()

    def test_lock_when_locked_raises(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "x"})
        v = Vault("ws1", vault_path)
        with pytest.raises(VaultError):
            v.lock()


class TestChangePassword:
    def test_change_password_locked(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "old", {"a.txt": "x"})
        v = Vault("ws1", vault_path)
        v.change_password("old", "new")
        with pytest.raises(VaultError):
            v.unlock("old")
        tree = v.unlock("new")
        assert tree.read_bytes("a.txt").decode() == "x"
        v.lock()

    def test_change_password_unlocked_keeps_edits(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "old", {"a.txt": "hello"})
        v = Vault("ws1", vault_path)
        tree = v.unlock("old")
        tree.write_bytes("b.txt", b"edited")
        v.change_password("old", "new")
        assert v.is_unlocked  # still unlocked after change

        v.lock()
        with pytest.raises(VaultError):
            v.unlock("old")
        tree2 = v.unlock("new")
        assert tree2.read_bytes("a.txt").decode() == "hello"
        assert tree2.read_bytes("b.txt").decode() == "edited"
        v.lock()

    def test_change_password_wrong_old(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "correct", {"a.txt": "x"})
        v = Vault("ws1", vault_path)
        with pytest.raises(VaultError):
            v.change_password("wrong", "new")


class TestManager:
    def test_get_returns_same_instance(self, tmp_path):
        m = VaultManager()
        p = str(tmp_path / "a.vault")
        assert m.get("ws1", p) is m.get("ws1", p)

    def test_lock_all(self, tmp_path):
        v1 = make_vault_file(tmp_path, "ws1", "pw1", {"a.txt": "1"})
        v2 = make_vault_file(tmp_path, "ws2", "pw2", {"b.txt": "2"})
        m = VaultManager()
        a = m.get("ws1", v1)
        b = m.get("ws2", v2)
        a.unlock("pw1")
        b.unlock("pw2")
        m.lock_all()
        assert not a.is_unlocked and not b.is_unlocked

    def test_discard(self, tmp_path):
        m = VaultManager()
        p = str(tmp_path / "a.vault")
        v = m.get("ws1", p)
        assert v is m.get("ws1", p)
        m.discard("ws1")
        # After discard, a new instance is returned
        assert m.get("ws1", p) is not v
