"""Tests for vault crypto primitives: encrypt/decrypt roundtrip,
wrong-password and tamper detection, header validation, pack/unpack
roundtrip, and secure delete.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from texteditor.crypto import (
    VAULT_MAGIC,
    CryptoError,
    create_encrypted_workspace,
    decrypt_vault,
    decrypt_workspace_inplace,
    encrypt_vault,
    folder_has_plaintext,
    merge_into_vault,
    pack_directory,
    secure_delete_dir,
    secure_delete_file,
    unpack_directory,
)


class TestEncryptDecrypt:
    def test_roundtrip(self):
        data = b"hello world" * 100
        vault = encrypt_vault(data, "test-password")
        assert vault[:4] == b"SLVC"
        assert decrypt_vault(vault, "test-password") == data

    def test_roundtrip_empty_plaintext(self):
        vault = encrypt_vault(b"", "pw")
        assert decrypt_vault(vault, "pw") == b""

    def test_wrong_password_fails(self):
        vault = encrypt_vault(b"secret", "correct")
        with pytest.raises(CryptoError):
            decrypt_vault(vault, "wrong")

    def test_tamper_detection_ciphertext(self):
        vault = bytearray(encrypt_vault(b"secret", "pw"))
        vault[-1] ^= 0xFF  # flip a bit in ciphertext
        with pytest.raises(CryptoError):
            decrypt_vault(bytes(vault), "pw")

    def test_tamper_detection_tag(self):
        vault = bytearray(encrypt_vault(b"secret", "pw"))
        # tag sits right before the ciphertext
        vault[4 + 16 + 12] ^= 0xFF
        with pytest.raises(CryptoError):
            decrypt_vault(bytes(vault), "pw")

    def test_bad_magic_rejected(self):
        vault = encrypt_vault(b"secret", "pw")
        bad = b"XXXX" + vault[4:]
        with pytest.raises(CryptoError):
            decrypt_vault(bad, "pw")

    def test_truncated_data_rejected(self):
        with pytest.raises(CryptoError):
            decrypt_vault(b"SLVC", "pw")

    def test_empty_data_rejected(self):
        with pytest.raises(CryptoError):
            decrypt_vault(b"", "pw")

    def test_unique_salt_and_nonce(self):
        v1 = encrypt_vault(b"data", "pw")
        v2 = encrypt_vault(b"data", "pw")
        assert v1[4:20] != v2[4:20]    # different salt
        assert v1[20:32] != v2[20:32]  # different nonce

    def test_no_plaintext_leakage(self):
        needle = b"super-secret-marker"
        vault = encrypt_vault(needle, "pw")
        assert needle not in vault


class TestPackUnpack:
    def test_roundtrip(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_text("content")
        (tmp_path / "top.txt").write_text("top")
        packed = pack_directory(str(tmp_path))
        out = tmp_path / "out"
        unpack_directory(packed, str(out))
        assert (out / "sub" / "file.txt").read_text() == "content"
        assert (out / "top.txt").read_text() == "top"

    def test_empty_dir(self, tmp_path):
        packed = pack_directory(str(tmp_path))
        out = tmp_path / "out"
        unpack_directory(packed, str(out))
        assert out.is_dir()

    def test_permissions_preserved(self, tmp_path):
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
        packed = pack_directory(str(tmp_path))
        out = tmp_path / "out"
        unpack_directory(packed, str(out))
        mode = out.joinpath("script.sh").stat().st_mode & 0o777
        assert mode == 0o755

    def test_non_directory_raises(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        with pytest.raises(CryptoError):
            pack_directory(str(f))

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(CryptoError):
            pack_directory(str(tmp_path / "nope"))


class TestSecureDelete:
    def test_removes_tree(self, tmp_path):
        (tmp_path / "secret.txt").write_text("sensitive")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "x.txt").write_text("x")
        secure_delete_dir(str(tmp_path))
        assert not tmp_path.exists()

    def test_missing_path_is_noop(self, tmp_path):
        secure_delete_dir(str(tmp_path / "nope"))  # must not raise

    def test_readonly_file(self, tmp_path):
        f = tmp_path / "ro.txt"
        f.write_text("x")
        f.chmod(0o444)
        secure_delete_dir(str(tmp_path))  # must not raise on read-only files
        assert not tmp_path.exists()


class TestSecureDeleteFile:
    def test_removes_file(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("sensitive")
        secure_delete_file(str(f))
        assert not f.exists()

    def test_missing_is_noop(self, tmp_path):
        secure_delete_file(str(tmp_path / "nope"))  # must not raise


class TestCreateEncryptedWorkspace:
    def test_encrypts_and_purges_plaintext(self, tmp_path):
        d = tmp_path / "ws"
        (d / "sub").mkdir(parents=True)
        (d / "top.txt").write_text("TOP")
        (d / "sub" / "inner.txt").write_text("INNER")
        vault = create_encrypted_workspace(str(d), "secret")
        assert os.path.isfile(vault)
        # folder now holds only the vault
        assert os.listdir(d) == [os.path.basename(vault)]
        # no plaintext leaked anywhere under the folder
        for dirpath, _dirnames, filenames in os.walk(d):
            for name in filenames:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    assert b"TOP" not in fh.read()
                    assert b"INNER" not in fh.read()

    def test_roundtrip_restores_files(self, tmp_path):
        d = tmp_path / "ws"
        (d / "sub").mkdir(parents=True)
        (d / "top.txt").write_text("TOP")
        (d / "sub" / "inner.txt").write_text("INNER")
        vault = create_encrypted_workspace(str(d), "secret")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "top.txt").read_text() == "TOP"
        assert (out / "sub" / "inner.txt").read_text() == "INNER"

    def test_wrong_password_fails(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("x")
        vault = create_encrypted_workspace(str(d), "correct")
        with pytest.raises(CryptoError):
            decrypt_vault(open(vault, "rb").read(), "wrong")

    def test_empty_password_rejected(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        with pytest.raises(CryptoError):
            create_encrypted_workspace(str(d), "")

    def test_non_directory_rejected(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        with pytest.raises(CryptoError):
            create_encrypted_workspace(str(f), "pw")


class TestFolderHasPlaintext:
    def test_plain_folder_is_true(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        assert folder_has_plaintext(str(tmp_path)) is True

    def test_vault_only_is_false(self, tmp_path):
        (tmp_path / ".subline.vault").write_bytes(b"SLVC" + b"\x00" * 60)
        assert folder_has_plaintext(str(tmp_path)) is False

    def test_mixed_is_true(self, tmp_path):
        (tmp_path / ".subline.vault").write_bytes(b"SLVC" + b"\x00" * 60)
        (tmp_path / "new.txt").write_text("x")
        assert folder_has_plaintext(str(tmp_path)) is True

    def test_empty_folder_is_false(self, tmp_path):
        assert folder_has_plaintext(str(tmp_path)) is False

    def test_missing_folder_is_false(self, tmp_path):
        assert folder_has_plaintext(str(tmp_path / "nope")) is False


class TestDecryptWorkspaceInplace:
    def test_roundtrip(self, tmp_path):
        d = tmp_path / "ws"
        (d / "sub").mkdir(parents=True)
        (d / "top.txt").write_text("TOP")
        (d / "sub" / "inner.txt").write_text("INNER")
        create_encrypted_workspace(str(d), "secret")
        assert not (d / "top.txt").exists()
        result = decrypt_workspace_inplace(str(d), "secret")
        assert result == str(d)
        assert not (d / ".subline.vault").exists()
        assert (d / "top.txt").read_text() == "TOP"
        assert (d / "sub" / "inner.txt").read_text() == "INNER"

    def test_wrong_password_raises_and_vault_intact(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("x")
        create_encrypted_workspace(str(d), "correct")
        with pytest.raises(CryptoError):
            decrypt_workspace_inplace(str(d), "wrong")
        assert (d / ".subline.vault").is_file()
        assert not (d / "a.txt").exists()

    def test_missing_vault_raises(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        with pytest.raises(CryptoError):
            decrypt_workspace_inplace(str(d), "pw")

    def test_empty_password_rejected(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / ".subline.vault").write_bytes(b"SLVC" + b"\x00" * 60)
        with pytest.raises(CryptoError):
            decrypt_workspace_inplace(str(d), "")

    def test_non_directory_rejected(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        with pytest.raises(CryptoError):
            decrypt_workspace_inplace(str(f), "pw")


class TestMergeIntoVault:
    def test_basic_new_files_added(self, tmp_path):
        d = tmp_path / "ws"
        (d / "orig").mkdir(parents=True)
        (d / "orig" / "a.txt").write_text("A")
        vault = create_encrypted_workspace(str(d), "secret")
        # add plaintext files next to the vault
        (d / "new.txt").write_text("NEW")
        (d / "sub2").mkdir()
        (d / "sub2" / "b.txt").write_text("B")
        assert folder_has_plaintext(str(d))
        merge_into_vault(str(d), "secret", lambda rel: "overwrite")
        assert os.listdir(d) == [os.path.basename(vault)]
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "orig" / "a.txt").read_text() == "A"
        assert (out / "new.txt").read_text() == "NEW"
        assert (out / "sub2" / "b.txt").read_text() == "B"

    def test_collision_overwrite(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("VAULT-VERSION")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("PLAIN-VERSION")
        merge_into_vault(str(d), "secret", lambda rel: "overwrite")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "a.txt").read_text() == "PLAIN-VERSION"

    def test_collision_keep_vault(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("VAULT-VERSION")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("PLAIN-VERSION")
        merge_into_vault(str(d), "secret", lambda rel: "keep_vault")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "a.txt").read_text() == "VAULT-VERSION"

    def test_collision_keep_both(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("VAULT-VERSION")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("PLAIN-VERSION")
        merge_into_vault(str(d), "secret", lambda rel: "keep_both")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "a.txt").read_text() == "VAULT-VERSION"
        assert (out / "a (1).txt").read_text() == "PLAIN-VERSION"

    def test_collision_keep_both_increments_suffix(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("V")
        (d / "a (1).txt").write_text("V1")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("P")
        merge_into_vault(str(d), "secret", lambda rel: "keep_both")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "a.txt").read_text() == "V"
        assert (out / "a (1).txt").read_text() == "V1"
        # "a (1).txt" already taken, so renamed to "a (2).txt"
        assert (out / "a (2).txt").read_text() == "P"

    def test_folder_merge_union(self, tmp_path):
        d = tmp_path / "ws"
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "v.txt").write_text("VAULT")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "sub").mkdir(exist_ok=True)
        (d / "sub" / "p.txt").write_text("PLAIN")
        merge_into_vault(str(d), "secret", lambda rel: "overwrite")
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "sub" / "v.txt").read_text() == "VAULT"
        assert (out / "sub" / "p.txt").read_text() == "PLAIN"

    def test_plaintext_securely_deleted_after_merge(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("SECRET-DATA")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "leak.txt").write_text("SECRET-DATA")
        (d / "sub").mkdir()
        (d / "sub" / "leak2.txt").write_text("SECRET-DATA")
        merge_into_vault(str(d), "secret", lambda rel: "overwrite")
        for dirpath, _dirnames, filenames in os.walk(d):
            for name in filenames:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    assert b"SECRET-DATA" not in fh.read()

    def test_wrong_password_raises_and_plaintext_intact(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("VAULT")
        vault = create_encrypted_workspace(str(d), "correct")
        (d / "new.txt").write_text("PLAIN")
        with pytest.raises(CryptoError):
            merge_into_vault(str(d), "wrong", lambda rel: "overwrite")
        assert (d / "new.txt").is_file()
        assert (d / ".subline.vault").is_file()

    def test_invalid_resolution_raises(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("V")
        vault = create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("P")
        with pytest.raises(CryptoError):
            merge_into_vault(str(d), "secret", lambda rel: "bogus")
        # vault must still be intact and decryptable
        data = decrypt_vault(open(vault, "rb").read(), "secret")
        out = tmp_path / "restored"
        unpack_directory(data, str(out))
        assert (out / "a.txt").read_text() == "V"

    def test_none_resolution_raises(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("V")
        create_encrypted_workspace(str(d), "secret")
        (d / "a.txt").write_text("P")
        with pytest.raises(CryptoError):
            merge_into_vault(str(d), "secret", lambda rel: None)

    def test_empty_password_rejected(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        with pytest.raises(CryptoError):
            merge_into_vault(str(d), "", lambda rel: "overwrite")

    def test_missing_vault_raises(self, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "a.txt").write_text("x")
        with pytest.raises(CryptoError):
            merge_into_vault(str(d), "pw", lambda rel: "overwrite")
