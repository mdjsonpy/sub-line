"""Encryption primitives for vault files.

Vault file layout (all little-endian, fixed-size header):

    4B magic ("SLVC") | 16B salt (PBKDF2) | 12B nonce (AES-GCM)
    | 16B tag (AES-GCM) | ciphertext (tar bytes)

Uses the ``cryptography`` library:
  - ``PBKDF2HMAC`` for key derivation (SHA-256, 600_000 iterations)
  - ``AESGCM`` (AES-256-GCM) for authenticated encryption

No Qt imports; pure Python and fully unit-testable.
"""
from __future__ import annotations

import io
import os
import shutil
import tarfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VAULT_MAGIC = b"SLVC"
PBKDF2_ITERATIONS = 600_000
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
KEY_LEN = 32

_HEADER_LEN = len(VAULT_MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN


class CryptoError(Exception):
    """Raised when a vault cannot be validated or decrypted."""


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from ``password`` and ``salt`` via PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_vault(plaintext: bytes, password: str) -> bytes:
    """Encrypt ``plaintext`` with ``password`` and return the full vault bytes.

    Output layout: MAGIC | salt | nonce | tag | ciphertext.
    """
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(password, salt)
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext, None)
    ciphertext = ct_and_tag[:-TAG_LEN]
    tag = ct_and_tag[-TAG_LEN:]
    return VAULT_MAGIC + salt + nonce + tag + ciphertext


def decrypt_vault(data: bytes, password: str) -> bytes:
    """Decrypt vault bytes produced by :func:`encrypt_vault`.

    Raises :class:`CryptoError` on a bad magic, truncated data, wrong
    password, or tampered ciphertext.
    """
    if len(data) < _HEADER_LEN:
        raise CryptoError("not a valid vault file (too small)")
    if data[: len(VAULT_MAGIC)] != VAULT_MAGIC:
        raise CryptoError("not a valid vault file (bad magic)")
    off = len(VAULT_MAGIC)
    salt = data[off : off + SALT_LEN]
    off += SALT_LEN
    nonce = data[off : off + NONCE_LEN]
    off += NONCE_LEN
    tag = data[off : off + TAG_LEN]
    off += TAG_LEN
    ciphertext = data[off:]
    key = derive_key(password, salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, None)
    except Exception as exc:
        raise CryptoError(
            "decryption failed (wrong password or corrupt vault)"
        ) from exc


def pack_directory(path: str) -> bytes:
    """Serialize the directory tree at ``path`` into tar bytes.

    Preserves file names, structure, content, permissions, and symlinks.
    """
    if not os.path.isdir(path):
        raise CryptoError(f"not a directory: {path}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(path, arcname=".", recursive=True)
    return buf.getvalue()


def unpack_directory(data: bytes, dest: str) -> None:
    """Extract tar bytes from :func:`pack_directory` into ``dest``.

    Creates ``dest`` if needed. Rejects entries that escape ``dest``
    (path traversal) via the ``data`` filter.
    """
    os.makedirs(dest, exist_ok=True)
    buf = io.BytesIO(data)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        tar.extractall(dest, filter="data")


def secure_delete_file(path: str) -> None:
    """Overwrite a file's bytes with random data, then unlink it."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            if os.path.islink(path):
                os.unlink(path)
            return
        size = os.path.getsize(path)
        chunk = 1 << 20
        with open(path, "r+b") as fh:
            offset = 0
            while offset < size:
                n = min(chunk, size - offset)
                fh.seek(offset)
                fh.write(os.urandom(n))
                offset += n
            fh.flush()
            os.fsync(fh.fileno())
        os.unlink(path)
    except OSError:
        pass


def secure_delete_dir(path: str) -> None:
    """Overwrite every regular file under ``path`` with random bytes,
    then delete the whole tree.

    Silently ignores read-only or already-gone entries. A missing
    ``path`` is a no-op.
    """
    if not os.path.isdir(path):
        return
    chunk = 1 << 20
    for dirpath, _dirnames, filenames in os.walk(path, topdown=False):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                if os.path.islink(fp) or not os.path.isfile(fp):
                    continue
                size = os.path.getsize(fp)
                with open(fp, "r+b") as fh:
                    offset = 0
                    while offset < size:
                        n = min(chunk, size - offset)
                        fh.seek(offset)
                        fh.write(os.urandom(n))
                        offset += n
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _purge_except(folder: str, keep_name: str) -> None:
    """Securely delete every entry under ``folder`` except ``keep_name``.

    ``folder`` itself is preserved (recreated if needed).
    """
    keep_abs = os.path.abspath(os.path.join(folder, keep_name))
    chunk = 1 << 20
    for dirpath, _dirnames, filenames in os.walk(folder, topdown=False):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            if os.path.abspath(fp) == keep_abs:
                continue
            try:
                if os.path.islink(fp) or not os.path.isfile(fp):
                    if os.path.islink(fp):
                        os.unlink(fp)
                    continue
                with open(fp, "r+b") as fh:
                    size = os.path.getsize(fp)
                    offset = 0
                    while offset < size:
                        n = min(chunk, size - offset)
                        fh.seek(offset)
                        fh.write(os.urandom(n))
                        offset += n
                    fh.flush()
                    os.fsync(fh.fileno())
                os.unlink(fp)
            except OSError:
                pass
    for dirpath, dirnames, _filenames in os.walk(folder, topdown=False):
        for name in dirnames:
            dp = os.path.join(dirpath, name)
            try:
                if not os.path.islink(dp) and os.path.isdir(dp) and not os.listdir(dp):
                    os.rmdir(dp)
            except OSError:
                pass


def create_encrypted_workspace(folder: str, password: str) -> str:
    """Encrypt ``folder`` in place into ``<folder>/.subline.vault`` and
    securely delete all plaintext entries (keeping only the vault).

    Returns the vault file path. The folder remains, now holding a single
    encrypted ``.subline.vault`` file.
    """
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise CryptoError(f"not a directory: {folder}")
    if not password:
        raise CryptoError("password must not be empty")
    tar = pack_directory(folder)
    data = encrypt_vault(tar, password)
    vault_file = os.path.join(folder, ".subline.vault")
    tmp = vault_file + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, vault_file)
    _purge_except(folder, ".subline.vault")
    return vault_file


def folder_has_plaintext(folder: str) -> bool:
    """Return True if ``folder`` contains any entry other than
    ``.subline.vault`` (i.e. the workspace is in "mixed" state).
    """
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return False
    try:
        entries = os.listdir(folder)
    except OSError:
        return False
    return any(name != ".subline.vault" for name in entries)


def decrypt_workspace_inplace(folder: str, password: str) -> str:
    """Decrypt ``<folder>/.subline.vault`` in place and extract the
    contents into ``folder``, then securely delete the vault file.

    Returns the folder path. On a wrong password or corrupt vault,
    raises :class:`CryptoError` and leaves the vault intact.
    """
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise CryptoError(f"not a directory: {folder}")
    if not password:
        raise CryptoError("password must not be empty")
    vault_file = os.path.join(folder, ".subline.vault")
    if not os.path.isfile(vault_file):
        raise CryptoError(f"vault file not found: {vault_file}")
    with open(vault_file, "rb") as fh:
        data = fh.read()
    tar = decrypt_vault(data, password)
    unpack_directory(tar, folder)
    secure_delete_file(vault_file)
    return folder


def _next_renamed(path: str) -> str:
    """Return ``path`` with a numeric suffix `` (1)``, `` (2)``, ...
    inserted before the extension, picking the first name that does not
    exist on disk.
    """
    directory, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        candidate = os.path.join(directory, f"{stem} ({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def merge_into_vault(
    folder: str,
    password: str,
    resolve_collision,
) -> str:
    """Merge all plaintext files in ``folder`` into
    ``<folder>/.subline.vault``, then securely delete the plaintext.

    ``resolve_collision(rel_path)`` is called for every path that exists
    in both vault and plaintext. It must return one of ``"overwrite"``,
    ``"keep_vault"``, or ``"keep_both"``. Any other return value (or a
    raised exception) aborts the merge and raises :class:`CryptoError`.

    Returns the vault file path. The folder is left containing only
    ``.subline.vault``.
    """
    import tempfile

    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise CryptoError(f"not a directory: {folder}")
    if not password:
        raise CryptoError("password must not be empty")
    vault_file = os.path.join(folder, ".subline.vault")
    if not os.path.isfile(vault_file):
        raise CryptoError(f"vault file not found: {vault_file}")
    with open(vault_file, "rb") as fh:
        data = fh.read()
    tar = decrypt_vault(data, password)

    tmpdir = tempfile.mkdtemp(prefix="subline-merge-")
    try:
        unpack_directory(tar, tmpdir)
        for dirpath, _dirnames, filenames in os.walk(folder):
            for name in filenames:
                if name == ".subline.vault":
                    continue
                src = os.path.join(dirpath, name)
                rel = os.path.relpath(src, folder)
                dest = os.path.join(tmpdir, rel)
                if os.path.exists(dest):
                    resolution = resolve_collision(rel)
                    if resolution == "overwrite":
                        if os.path.isdir(dest) and not os.path.islink(dest):
                            shutil.rmtree(dest)
                        elif os.path.exists(dest):
                            os.unlink(dest)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(src, dest)
                    elif resolution == "keep_vault":
                        pass
                    elif resolution == "keep_both":
                        dest = _next_renamed(dest)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(src, dest)
                    else:
                        raise CryptoError(
                            f"invalid collision resolution for {rel!r}: {resolution!r}"
                        )
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
        merged_tar = pack_directory(tmpdir)
        merged_data = encrypt_vault(merged_tar, password)
        tmp = vault_file + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(merged_data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, vault_file)
        _purge_except(folder, ".subline.vault")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return vault_file
