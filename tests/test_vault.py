"""Tests for vault lifecycle: unlock/lock cycles, password changes,
manager registry, and orphan recovery.
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


def read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


class TestUnlockLock:
    def test_unlock_lock_cycle(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "mypw", {"a.txt": "hello"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        assert not v.is_unlocked
        assert v.temp_path is None

        temp = v.unlock("mypw")
        assert v.is_unlocked
        assert v.temp_path == temp
        assert read(os.path.join(temp, "a.txt")) == "hello"

        with open(os.path.join(temp, "b.txt"), "w") as fh:
            fh.write("new file")

        v.lock()
        assert not v.is_unlocked
        assert v.temp_path is None
        assert not os.path.exists(temp)

        # Re-unlock: both original and modified files must be present
        temp2 = v.unlock("mypw")
        assert read(os.path.join(temp2, "a.txt")) == "hello"
        assert read(os.path.join(temp2, "b.txt")) == "new file"
        v.lock()

    def test_unlock_wrong_password(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "correct", {"a.txt": "x"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        with pytest.raises(VaultError):
            v.unlock("wrong")
        assert not v.is_unlocked

    def test_unlock_missing_vault_file(self, tmp_path):
        v = Vault("ws1", str(tmp_path / "nope.vault"),
                  base_dir=str(tmp_path / "vaults"))
        with pytest.raises(VaultError):
            v.unlock("pw")
        assert not v.is_unlocked

    def test_unlock_replaces_stale_temp_dir(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "hello"})
        base = str(tmp_path / "vaults")
        stale = os.path.join(base, "ws1")
        os.makedirs(stale)
        with open(os.path.join(stale, "stale.txt"), "w") as fh:
            fh.write("old crash data")

        v = Vault("ws1", vault_path, base_dir=base)
        temp = v.unlock("pw")
        assert not os.path.exists(os.path.join(temp, "stale.txt"))
        assert read(os.path.join(temp, "a.txt")) == "hello"
        v.lock()

    def test_lock_when_locked_raises(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "x"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        with pytest.raises(VaultError):
            v.lock()

    def test_lock_writes_no_plaintext_temp(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "secret-data"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        temp = v.unlock("pw")
        v.lock()
        # No plaintext may remain anywhere under the base dir
        base = str(tmp_path / "vaults")
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    assert b"secret-data" not in fh.read()


class TestChangePassword:
    def test_change_password_locked(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "old", {"a.txt": "x"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        v.change_password("old", "new")
        with pytest.raises(VaultError):
            v.unlock("old")
        temp = v.unlock("new")
        assert read(os.path.join(temp, "a.txt")) == "x"
        v.lock()

    def test_change_password_unlocked_keeps_edits(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "old", {"a.txt": "hello"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        temp = v.unlock("old")
        with open(os.path.join(temp, "b.txt"), "w") as fh:
            fh.write("edited")
        v.change_password("old", "new")
        assert v.is_unlocked  # still unlocked after change

        v.lock()
        with pytest.raises(VaultError):
            v.unlock("old")
        temp2 = v.unlock("new")
        assert read(os.path.join(temp2, "a.txt")) == "hello"
        assert read(os.path.join(temp2, "b.txt")) == "edited"
        v.lock()

    def test_change_password_wrong_old(self, tmp_path):
        vault_path = make_vault_file(tmp_path, "ws1", "correct", {"a.txt": "x"})
        v = Vault("ws1", vault_path, base_dir=str(tmp_path / "vaults"))
        with pytest.raises(VaultError):
            v.change_password("wrong", "new")


class _FakeWs:
    def __init__(self, ws_id: str, vault_path: str, encrypted: bool = True):
        self.id = ws_id
        self.vault_path = vault_path
        self.root = vault_path
        self.encrypted = encrypted


class TestManager:
    def test_get_returns_same_instance(self, tmp_path):
        m = VaultManager(base_dir=str(tmp_path / "vaults"))
        p = str(tmp_path / "a.vault")
        assert m.get("ws1", p) is m.get("ws1", p)

    def test_lock_all(self, tmp_path):
        v1 = make_vault_file(tmp_path, "ws1", "pw1", {"a.txt": "1"})
        v2 = make_vault_file(tmp_path, "ws2", "pw2", {"b.txt": "2"})
        m = VaultManager(base_dir=str(tmp_path / "vaults"))
        a = m.get("ws1", v1)
        b = m.get("ws2", v2)
        a.unlock("pw1")
        b.unlock("pw2")
        m.lock_all()
        assert not a.is_unlocked and not b.is_unlocked

    def test_recover_from_workspaces(self, tmp_path):
        base = str(tmp_path / "vaults")
        m = VaultManager(base_dir=base)
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "x"})
        ws = _FakeWs("ws1", vault_path)

        # No temp dir yet: nothing to recover
        assert m.recover_from_workspaces([ws]) == []

        # Simulate a crash: temp dir on disk, no unlocked vault
        orphan = os.path.join(base, "ws1")
        os.makedirs(os.path.join(orphan, "sub"))
        with open(os.path.join(orphan, "sub", "data.txt"), "w") as fh:
            fh.write("unsaved")
        assert m.recover_from_workspaces([ws]) == ["ws1"]
        # temp dir must be wiped after recovery
        assert not os.path.isdir(orphan)
        # vault file still present (re-encrypted with recovery key)
        assert os.path.isfile(vault_path) and os.path.getsize(vault_path) > 0

    def test_recover_skips_unlocked(self, tmp_path):
        base = str(tmp_path / "vaults")
        m = VaultManager(base_dir=base)
        vault_path = make_vault_file(tmp_path, "ws1", "pw", {"a.txt": "x"})
        ws = _FakeWs("ws1", vault_path)
        v = m.get("ws1", vault_path)
        v.unlock("pw")  # now unlocked, temp dir exists
        assert os.path.isdir(os.path.join(base, "ws1"))
        # an unlocked vault is never "orphaned"
        assert m.recover_from_workspaces([ws]) == []
        v.lock()

    def test_recover_skips_plain(self, tmp_path):
        base = str(tmp_path / "vaults")
        m = VaultManager(base_dir=base)
        ws_plain = _FakeWs("ws1", str(tmp_path / "plain.vault"), encrypted=False)
        orphan = os.path.join(base, "ws1")
        os.makedirs(orphan)
        assert m.recover_from_workspaces([ws_plain]) == []
        assert os.path.isdir(orphan)  # left untouched

    def test_discard_orphan(self, tmp_path):
        base = str(tmp_path / "vaults")
        m = VaultManager(base_dir=base)
        orphan = os.path.join(base, "ws9")
        os.makedirs(orphan)
        with open(os.path.join(orphan, "f.txt"), "w") as fh:
            fh.write("junk")
        m.discard_orphan("ws9")
        assert not os.path.exists(orphan)
