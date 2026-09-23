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
    encrypt_vault,
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
