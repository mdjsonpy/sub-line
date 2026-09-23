# Encrypted Workspaces — Implementation Plan

## Overview

Add support for encrypted workspaces where **both file/folder names and file contents**
are encrypted at rest. Each encrypted workspace is stored as a single `.vault` file.
When unlocked, the workspace is extracted to a temp directory and behaves identically
to a normal workspace. Encrypted workspaces are visually distinguished by **red text**
and a **padlock icon** in the sidebar.

### Decisions

- **Per-workspace password** — each encrypted workspace has its own password
- **Full encryption** — names + content (entire workspace packed as tar, then encrypted)
- **Locked until clicked** — encrypted workspaces appear locked in sidebar; user double-clicks to enter password and unlock

---

## Vault File Format

```
┌──────────┬────────────┬────────────┬────────────┬─────────────────────────┐
│ 4B magic │ 16B salt   │ 12B nonce  │ 16B tag    │ ciphertext (tar bytes)  │
│ "SLVC"   │ (PBKDF2)   │ (AES-GCM)  │ (AES-GCM)  │                         │
└──────────┴────────────┴────────────┴────────────┴─────────────────────────┘
```

- **Magic**: `b"SLVC"` (SubLine Vault Container)
- **Salt**: 16 random bytes, used for PBKDF2 key derivation
- **Nonce**: 12 random bytes, used for AES-256-GCM
- **Tag**: 16-byte authentication tag from AES-GCM
- **Ciphertext**: the encrypted tar archive of the workspace

### Key Derivation

```
key = PBKDF2-HMAC-SHA256(password_utf8, salt, iterations=600_000, dklen=32)
```

### Encryption

```
ciphertext, tag = AES-256-GCM(key, nonce, tar_bytes)
```

### Decryption

```
tar_bytes = AES-256-GCM-decrypt(key, nonce, ciphertext, tag)  # raises on auth failure
```

---

## New Files

### 1. `texteditor/crypto.py` (~120 lines)

Core crypto primitives. No Qt imports. Pure Python, fully unit-testable.

```python
"""Encryption primitives for vault files.

Uses the `cryptography` library:
  - PBKDF2HMAC for key derivation
  - AESGCM for authenticated encryption
"""

VOLT_MAGIC = b"SLVC"
PBKDF2_ITERATIONS = 600_000
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
KEY_LEN = 32

class CryptoError(Exception): ...

def derive_key(password: str, salt: bytes) -> bytes
def encrypt_vault(plaintext: bytes, password: str) -> bytes
def decrypt_vault(data: bytes, password: str) -> bytes
def pack_directory(path: str) -> bytes
def unpack_directory(data: bytes, dest: str) -> None
def secure_delete_dir(path: str) -> None
```

**Details:**

- `derive_key`: PBKDF2-HMAC-SHA256, 600K iterations, 32-byte output
- `encrypt_vault`: generates random salt + nonce, derives key, AES-GCM encrypts, returns `MAGIC + salt + nonce + tag + ciphertext`
- `decrypt_vault`: validates magic, parses header, derives key, AES-GCM decrypts (raises `CryptoError` on bad password or tampered data)
- `pack_directory`: uses `tarfile` to create a tar archive of the directory (preserves names, structure, content, permissions, symlinks)
- `unpack_directory`: extracts tar bytes to destination dir (creates it if needed)
- `secure_delete_dir`: overwrites every file in the directory tree with random bytes, then `shutil.rmtree`

### 2. `texteditor/vault.py` (~120 lines)

Vault lifecycle management. No Qt imports.

```python
"""Vault management: unlock/lock encrypted workspaces."""

class VaultError(Exception): ...

class Vault:
    def __init__(self, workspace_id: str, vault_path: str) -> None
    def unlock(self, password: str) -> str
    def lock(self) -> None
    def change_password(self, old_password: str, new_password: str) -> None
    @property
    def is_unlocked(self) -> bool
    @property
    def temp_path(self) -> str | None
    @property
    def vault_path(self) -> str

class VaultManager:
    def __init__(self, base_dir: str | None = None) -> None
    def get(self, workspace_id: str, vault_path: str) -> Vault
    def lock_all(self) -> None
    def recover_orphaned(self) -> list[str]
```

**Details:**

- `Vault.unlock(password)`:
  1. Read vault file from disk
  2. Call `crypto.decrypt_vault(data, password)` → tar bytes
  3. Write tar to `<base>/vaults/<ws_id>.tar`
  4. Call `crypto.unpack_directory(tar_bytes, <base>/vaults/<ws_id>/)`
  5. Return the temp dir path
  6. Raise `VaultError` on wrong password or corrupt vault

- `Vault.lock()`:
  1. Call `crypto.pack_directory(self._temp_path)` → tar bytes
  2. Call `crypto.encrypt_vault(tar_bytes, self._password)` → vault bytes
  3. Atomic write to `self._vault_path` (temp + `os.replace`)
  4. Call `crypto.secure_delete_dir(self._temp_path)`
  5. Clear `self._key`, `self._password`, `self._temp_path`

- `Vault.change_password(old, new)`:
  1. Verify old password by attempting decrypt
  2. Re-encrypt with new password
  3. Update stored password reference

- `VaultManager.recover_orphaned()`:
  1. Scan `<base>/vaults/` for directories matching known workspace IDs
  2. Return list of orphaned workspace IDs (temp dirs without a corresponding unlocked vault)
  3. Caller decides: recover (keep) or discard (secure delete)

- **Temp dir location**: `~/.local/share/subline/vaults/<workspace_id>/`
  (respects `XDG_DATA_HOME`)

### 3. `texteditor/password_dialog.py` (~70 lines)

Qt password input dialogs.

```python
"""Password dialogs for vault unlock and password change."""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton

class PasswordDialog(QDialog):
    """Single password input dialog."""
    @staticmethod
    def get_password(parent, title: str, prompt: str = "Password:") -> str | None

class ChangePasswordDialog(QDialog):
    """Old + new + confirm password dialog."""
    @staticmethod
    def get(parent, title: str) -> tuple[str, str] | None
```

**Details:**

- `PasswordDialog`: one `QLineEdit` with `EchoMode.Password`, OK/Cancel buttons
  - Returns password string on OK, `None` on Cancel
- `ChangePasswordDialog`: three `QLineEdit`s (old, new, confirm) with `EchoMode.Password`
  - Validates new == confirm before returning
  - Returns `(old_password, new_password)` on OK, `None` on Cancel or mismatch

---

## Modified Files

### 4. `texteditor/store.py` (~15 lines added)

Add `encrypted` field to `Workspace` dataclass.

```python
@dataclass
class Workspace:
    id: str
    name: str | None = None
    root: str | None = None
    tabs: list = field(default_factory=list)
    active: int = 0
    splits: list = field(default_factory=list)
    split_actives: list = field(default_factory=list)
    encrypted: bool = False  # NEW
```

- When `encrypted=True`, `root` holds the path to the `.vault` file
- The temp dir is derived from `workspace_id` at runtime (not stored)
- Bump schema version to 4
- `_parse_workspace`: read `encrypted` field (default `False` for v3 files)
- `save`: write `encrypted` field

### 5. `texteditor/workspace_list.py` (~45 lines changed)

Visual distinction + new context menu actions.

**New role constant:**
```python
ROLE_ENCRYPTED = Qt.UserRole + 4
```

**`_make_workspace` changes:**
```python
def _make_workspace(self, parent, ws_id, label, root, encrypted=False):
    item = QTreeWidgetItem(parent, [label])
    # ... existing data ...
    item.setData(0, ROLE_ENCRYPTED, encrypted)
    if encrypted:
        from PySide6.QtGui import QColor
        item.setForeground(0, QColor("#e06060"))
        item.setText(0, f"\U0001F512 {label}")  # padlock emoji
    return item
```

**`set_model` changes:**
- Pass `ws.encrypted` to `_make_workspace`

**New signals:**
```python
unlockRequested = Signal(str)          # workspace id
lockRequested = Signal(str)            # workspace id
changePasswordRequested = Signal(str)  # workspace id
```

**`_workspace_menu` changes:**
- If `item.data(0, ROLE_ENCRYPTED)`:
  - Add "Unlock…" → emits `unlockRequested`
  - Add "Lock" → emits `lockRequested`
  - Add "Change Password…" → emits `changePasswordRequested`
  - Hide "Open in File Explorer" and "Open Terminal Here" (no meaningful folder when locked)
  - Hide "Copy Absolute Path" (would reveal vault path)

### 6. `texteditor/main_window.py` (~70 lines added)

Orchestrate unlock/lock/create flows.

**New imports:**
```python
from .vault import Vault, VaultManager, VaultError
from .password_dialog import PasswordDialog, ChangePasswordDialog
```

**`__init__` additions:**
```python
self._vaults = VaultManager()
# Connect new signals:
self._ws_list.unlockRequested.connect(self.unlock_workspace)
self._ws_list.lockRequested.connect(self.lock_workspace)
self._ws_list.changePasswordRequested.connect(self.change_password)
```

**New methods:**

```python
def new_encrypted_workspace(self) -> None:
    """Create a new encrypted workspace."""
    path, _ = QFileDialog.getSaveFileName(
        self, "Create Encrypted Workspace", os.path.expanduser("~"),
        "Vault files (*.vault);;All files (*)"
    )
    if not path:
        return
    if not path.endswith(".vault"):
        path += ".vault"
    result = ChangePasswordDialog.get(self, "Set Password")
    if result is None:
        return
    _old, new_password = result
    # Create empty vault
    from .crypto import encrypt_vault, pack_directory
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        tar_bytes = pack_directory(tmp)
    vault_data = encrypt_vault(tar_bytes, new_password)
    # Atomic write
    tmp_file = path + ".tmp"
    with open(tmp_file, "wb") as fh:
        fh.write(vault_data)
    os.replace(tmp_file, path)
    # Add to workspace list
    ws = Workspace(id=uuid.uuid4().hex, name=None, root=path,
                   tabs=[], active=0, encrypted=True)
    self._workspaces.append(ws)
    self._layout.append(LayoutEntry("workspace", ws.id))
    self._rebuild_by_id()
    # Register with vault manager (locked state)
    self._vaults.get(ws.id, path)
    self._refresh_list()
    self._schedule_save()

def unlock_workspace(self, ws_id: str) -> None:
    """Unlock an encrypted workspace."""
    ws = self._by_id.get(ws_id)
    if ws is None or not ws.encrypted:
        return
    vault = self._vaults.get(ws_id, ws.root)
    if vault.is_unlocked:
        # Already unlocked, just activate
        self.activate_workspace(ws_id)
        return
    password = PasswordDialog.get_password(self, f"Unlock '{ws.name or ws.root}'")
    if password is None:
        return
    try:
        temp_dir = vault.unlock(password)
    except VaultError as e:
        QMessageBox.warning(self, "Unlock Failed", str(e))
        return
    # Switch to this workspace with the temp dir as root
    self._activate_unlocked(ws_id, temp_dir)

def lock_workspace(self, ws_id: str) -> None:
    """Lock an unlocked encrypted workspace."""
    ws = self._by_id.get(ws_id)
    if ws is None or not ws.encrypted:
        return
    vault = self._vaults.get(ws_id, ws.root)
    if not vault.is_unlocked:
        return
    # If this is the current workspace, confirm + save first
    if self._current_ws_id() == ws_id:
        if not self._view.confirm_close_all():
            return
        self._view.save_all()
    vault.lock()
    if self._current_ws_id() == ws_id:
        self._view.close_all_tabs()
        self._view.workspace = None
        self._current_index = -1
    self._refresh_list()
    self._schedule_save()

def change_password(self, ws_id: str) -> None:
    """Change the password of an encrypted workspace."""
    ws = self._by_id.get(ws_id)
    if ws is None or not ws.encrypted:
        return
    vault = self._vaults.get(ws_id, ws.root)
    result = ChangePasswordDialog.get(self, f"Change Password for '{ws.name or ws.root}'")
    if result is None:
        return
    old_pw, new_pw = result
    try:
        vault.change_password(old_pw, new_pw)
    except VaultError as e:
        QMessageBox.warning(self, "Change Failed", str(e))

def _activate_unlocked(self, ws_id: str, temp_dir: str) -> None:
    """Activate a workspace whose vault is now unlocked."""
    # Confirm close of current workspace
    if self._current_index >= 0:
        if not self._view.confirm_close_all():
            return
        self._view.capture_into(self._workspaces[self._current_index])
    ws = self._by_id[ws_id]
    index = self._index_of(ws_id)
    self._current_index = index
    # Set the temp dir as the root (not the vault path)
    ws.root = temp_dir  # temporarily; will be restored on lock
    self._view.load_workspace(ws)
    self._refresh_list()
    self._schedule_save()
```

**`closeEvent` changes:**
```python
def closeEvent(self, event) -> None:
    # Lock all unlocked vaults before closing
    for ws in self._workspaces:
        if ws.encrypted:
            vault = self._vaults.get(ws.id, ws.root)
            if vault.is_unlocked:
                if self._current_ws_id() == ws.id:
                    if not self._view.confirm_close_all():
                        event.ignore()
                        return
                    self._view.save_all()
                try:
                    vault.lock()
                except Exception:
                    ret = QMessageBox.critical(
                        self, "Lock Failed",
                        f"Could not lock '{ws.name}'.\n"
                        "The temp data may be left on disk.\n"
                        "Quit anyway?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if ret != QMessageBox.Yes:
                        event.ignore()
                        return
    self._save_state()
    event.accept()
```

**`_build_menus` additions:**
- File menu: "New Encrypted Workspace…" → `self.new_encrypted_workspace`

**Startup orphan recovery (in `_load_workspaces` or `app.py`):**
```python
def _check_orphans(self) -> None:
    orphans = self._vaults.recover_orphaned()
    if not orphans:
        return
    for ws_id in orphans:
        ws = self._by_id.get(ws_id)
        name = ws.name if ws else ws_id
        ret = QMessageBox.question(
            self, "Recovery",
            f"Unsaved data found for encrypted workspace '{name}'.\n"
            "Recover (keep the data) or Discard (delete it)?",
            QMessageBox.Recover | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Recover
        )
        if ret == QMessageBox.Discard:
            self._vaults.discard_orphan(ws_id)
```

### 7. `pyproject.toml` (1 line)

Add to dependencies:
```toml
"cryptography>=42.0",
```

### 8. `texteditor/app.py` (~5 lines)

In the startup sequence (after `MainWindow` is created):
- Call `main_window._check_orphans()` to handle crash recovery

---

## Tests

### `tests/test_crypto.py` (~60 lines)

```python
def test_encrypt_decrypt_roundtrip():
    data = b"hello world" * 100
    pw = "test-password"
    vault = encrypt_vault(data, pw)
    assert vault[:4] == b"SLVC"
    assert decrypt_vault(vault, pw) == data

def test_wrong_password_fails():
    vault = encrypt_vault(b"secret", "correct")
    with pytest.raises(CryptoError):
        decrypt_vault(vault, "wrong")

def test_tamper_detection():
    vault = bytearray(encrypt_vault(b"secret", "pw"))
    vault[-1] ^= 0xFF  # flip a bit in ciphertext
    with pytest.raises(CryptoError):
        decrypt_vault(bytes(vault), "pw")

def test_unique_salt_and_nonce():
    v1 = encrypt_vault(b"data", "pw")
    v2 = encrypt_vault(b"data", "pw")
    assert v1[4:20] != v2[4:20]  # different salt
    assert v1[20:32] != v2[20:32]  # different nonce

def test_pack_unpack_roundtrip(tmp_path):
    # Create a test directory structure
    (tmp_path / "sub" / "file.txt").parent.mkdir(parents=True)
    (tmp_path / "sub" / "file.txt").write_text("content")
    (tmp_path / "top.txt").write_text("top")
    packed = pack_directory(str(tmp_path))
    out = tmp_path / "out"
    unpack_directory(packed, str(out))
    assert (out / "sub" / "file.txt").read_text() == "content"
    assert (out / "top.txt").read_text() == "top"

def test_secure_delete(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("sensitive")
    secure_delete_dir(str(tmp_path))
    assert not tmp_path.exists()
```

### `tests/test_vault.py` (~80 lines)

```python
def test_unlock_lock_cycle(tmp_path):
    # Create a vault with known content
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "a.txt").write_text("hello")
    tar = pack_directory(str(ws_dir))
    vault_path = str(tmp_path / "test.vault")
    with open(vault_path, "wb") as fh:
        fh.write(encrypt_vault(tar, "mypw"))
    
    base = str(tmp_path / "vaults")
    v = Vault("ws1", vault_path)
    v._base_dir = base
    
    temp = v.unlock("mypw")
    assert os.path.isfile(os.path.join(temp, "a.txt"))
    assert open(os.path.join(temp, "a.txt")).read() == "hello"
    assert v.is_unlocked
    
    # Modify a file in temp
    with open(os.path.join(temp, "b.txt"), "w") as fh:
        fh.write("new file")
    
    v.lock()
    assert not v.is_unlocked
    assert not os.path.exists(temp)
    
    # Re-unlock and verify both files
    temp2 = v.unlock("mypw")
    assert open(os.path.join(temp2, "a.txt")).read() == "hello"
    assert open(os.path.join(temp2, "b.txt")).read() == "new file"
    v.lock()

def test_change_password(tmp_path):
    # ... create vault, change password, verify old fails, new works

def test_wrong_password_raises(tmp_path):
    # ... vault.unlock("wrong") raises VaultError

def test_recover_orphaned(tmp_path):
    # ... create temp dir without unlocking, verify recover_orphaned finds it
```

### `tests/test_store_encrypted.py` (~40 lines)

```python
def test_v3_migration_defaults_encrypted_false():
    # Load a v3 workspaces.json, verify encrypted defaults to False

def test_encrypted_workspace_roundtrip(tmp_path):
    # Save a StoreData with an encrypted workspace, load it back, verify fields

def test_encrypted_field_persisted(tmp_path):
    # Verify the JSON file contains "encrypted": true
```

---

## Implementation Phases

Each phase is independently verifiable: finish a phase, run its tests,
commit. No phase depends on work from a later phase.

### Status Legend

- ✅ **Implemented** — code + tests in place, acceptance criteria met
- ⬜ **Pending** — not yet started

### Implementation Status

| Phase | Title | Status | Evidence |
|-------|-------|--------|----------|
| 1 | Foundation & Crypto Core | ✅ Implemented | `texteditor/crypto.py`, `tests/test_crypto.py` (incl. `create_encrypted_workspace`, `secure_delete_file`) |
| 2 | Vault Lifecycle | ✅ Implemented | `texteditor/vault.py`, `tests/test_vault.py` |
| 3 | Persistence | ✅ Implemented | `texteditor/store.py` (schema v4), `tests/test_store_encrypted.py` |
| 4 | UI Layer | ✅ Implemented | `texteditor/password_dialog.py`, `texteditor/new_workspace_dialog.py`, `texteditor/workspace_list.py` |
| 5 | Orchestration | ✅ Implemented | `texteditor/main_window.py` (new/unlock/lock/change-password/close/recover) |
| 6 | Verification & Hardening | ✅ Implemented | 118 tests green (`pytest tests/`) |

### Phase 1 — Foundation & Crypto Core ✅

**Goal:** a working, tested encrypt/decrypt roundtrip with no Qt dependencies.

| Step | File | Description |
|------|------|-------------|
| 1.1 | `pyproject.toml` | Add `cryptography>=42.0` dependency |
| 1.2 | `texteditor/crypto.py` | Core crypto primitives (no Qt) |
| 1.3 | `texteditor/crypto.py` | `create_encrypted_workspace` (encrypt a folder in place, purge plaintext) + `secure_delete_file` |
| 1.4 | `tests/test_crypto.py` | Unit tests for crypto layer (incl. `TestCreateEncryptedWorkspace`, `TestSecureDeleteFile`) |

**Acceptance criteria:**
- `pytest tests/test_crypto.py` passes
- `encrypt_vault` → `decrypt_vault` roundtrip works for arbitrary bytes
- Wrong password and tampered ciphertext both raise `CryptoError`
- `pack_directory` / `unpack_directory` preserve structure, names, content, permissions
- No Qt imports in `crypto.py`

### Phase 2 — Vault Lifecycle ✅

**Goal:** unlock/lock/change-password lifecycle on top of the crypto core.

| Step | File | Description |
|------|------|-------------|
| 2.1 | `texteditor/vault.py` | Vault + VaultManager (no Qt) |
| 2.2 | `tests/test_vault.py` | Unit tests for vault lifecycle |

**Acceptance criteria:**
- `pytest tests/test_vault.py` passes
- Unlock → modify files → lock → re-unlock preserves all changes
- `change_password`: old password rejected, new password accepted
- `lock()` atomically rewrites the vault file and secure-deletes the temp dir
- `recover_orphaned()` finds leftover temp dirs
- No Qt imports in `vault.py`

### Phase 3 — Persistence ✅

**Goal:** encrypted workspaces survive app restarts via the store.

| Step | File | Description |
|------|------|-------------|
| 3.1 | `texteditor/store.py` | Add `encrypted` field, bump schema to v4 |
| 3.2 | `tests/test_store_encrypted.py` | Migration + persistence tests |

**Acceptance criteria:**
- `pytest tests/test_store_encrypted.py` passes
- Full existing test suite still passes (v3 files load with `encrypted=False`)
- Roundtrip: save → load preserves `encrypted=True` and `.vault` root path

### Phase 4 — UI Layer ✅

**Goal:** user can see and interact with encrypted workspaces.

| Step | File | Description |
|------|------|-------------|
| 4.1 | `texteditor/password_dialog.py` | Qt password dialogs |
| 4.2 | `texteditor/workspace_list.py` | Red styling + padlock + context menu + signals |

**Acceptance criteria:**
- Encrypted workspaces render with red text and padlock prefix
- Context menu shows Unlock / Lock / Change Password for encrypted entries;
  hides explorer/terminal/copy-path actions
- `PasswordDialog` returns `None` on Cancel; `ChangePasswordDialog` rejects mismatched confirm
- Full existing test suite still passes

### Phase 5 — Orchestration ✅

**Goal:** the full user flow works end to end.

| Step | File | Description |
|------|------|-------------|
| 5.1 | `texteditor/main_window.py` | New-encrypted-workspace flow (`create_encrypted_workspace` + unlock), `_ensure_unlocked`, `_on_lock`/`_lock_workspace`, `_on_change_password`, `_recover_vaults` on startup, close locks (vault preserved) |
| 5.2 | `texteditor/main_window.py` | `open_workspace` / `new_workspace_from_folder` guards for encrypted entries |

**Acceptance criteria:**
- "File → New Encrypted Workspace…" creates a `.vault` and lists it locked
- Double-click / Unlock prompt: correct password opens the workspace; wrong shows error
- Locking the active workspace saves all tabs first, then wipes the temp dir
- Closing the app with an unlocked vault prompts to save, locks, then quits
- Crash simulation (kill -9 while unlocked) → next startup offers Recover/Discard

### Phase 6 — Verification & Hardening ✅

**Goal:** confidence that the whole feature is correct and secure.

| Step | File | Description |
|------|------|-------------|
| 6.1 | — | Run full test suite |
| 6.2 | — | Manual smoke test: create → edit → lock → restart → unlock → verify |
| 6.3 | — | Inspect `.vault` bytes: magic, header layout, no plaintext leakage |
| 6.4 | — | Verify no temp dir remains after lock or crash-recovery discard |
| 6.5 | — | Verify password never appears in logs, saved state, or exception messages |

**Acceptance criteria:**
- `pytest tests/` fully green
- Manual smoke test passes with no data loss
- `strings` on a vault file shows no plaintext file names or content

---

## Security Summary

| Property | Mechanism |
|----------|-----------|
| Content encryption at rest | AES-256-GCM |
| Name/structure encryption at rest | Encrypted tar (names only exist inside ciphertext) |
| Tamper detection | GCM authentication tag |
| Key derivation | PBKDF2-HMAC-SHA256, 600K iterations |
| Key storage | In-memory only (Python variable), never on disk |
| Salt uniqueness | 16 random bytes per vault |
| Nonce uniqueness | 12 random bytes per encryption |
| Plaintext cleanup | Secure delete (overwrite with random bytes + rmtree) |
| Atomic vault writes | Write to `.tmp` then `os.replace` |

### Not Protected (acceptable trade-offs)

- Vault file name on disk (e.g., `myproject.vault` is visible)
- File size (ciphertext size ≈ plaintext size + 48 bytes)
- File modification time
- Existence of the vault file

### Threat Model

This protects against:
- Someone with disk access reading your code/configs
- Forensic recovery of deleted vault files (encrypted)
- Accidental exposure of workspace contents

This does NOT protect against:
- Malicious software running with your user permissions
- Kernel-level memory scraping (key is in process memory while unlocked)
- Social engineering (password disclosure)
- Backups/syncs that copy the temp dir before lock

---

## Future Enhancements (out of scope)

- Keyfile support (password + file = key, for forgotten passwords)
- Auto-lock after N minutes of inactivity
- Progress dialog for large vaults (>1GB)
- Vault file versioning / migration
- Hardware-backed key storage (TPM / Secure Enclave)
- Zero-knowledge backup integration
- Per-file encryption (instead of whole-vault) for selective access
