# Encrypt / Decrypt Workspace — Feature Specification

## Overview

Add the ability to encrypt and decrypt an entire workspace folder in-place.
This is a **permanent state toggle** distinct from the existing Unlock/Lock
(temp-dir based, vault persists). Both features coexist.

| Action | Direction | Effect |
|--------|-----------|--------|
| **Encrypt…** | plain → encrypted | All files packed into `.subline.vault`, plaintext securely deleted |
| **Decrypt…** | encrypted → plain | `.subline.vault` extracted in-place, vault file deleted |
| **Add files to vault…** | mixed → encrypted | Plaintext files merged into vault, plaintext deleted |

---

## Workspace States

| State | Folder contents | `encrypted` flag | File tree |
|-------|----------------|-----------------|-----------|
| **Plain** | plaintext files only | `False` | folder |
| **Encrypted** | `.subline.vault` only | `True` | (locked) or temp dir (unlocked) |
| **Mixed** | `.subline.vault` + plaintext files | `True` | two sections (see below) |

Mixed state detection: `encrypted=True` AND the folder (parent of `vault_path`)
contains files other than `.subline.vault`.

---

## How Mixed State Arises

Both scenarios must be supported:

1. **Decrypt then add files**: Decrypt the workspace (all files extracted),
   create new files, then want to put everything back into the vault.
2. **Encrypt then add files**: Workspace is encrypted (vault exists), user
   creates new files directly in the folder alongside the vault.

---

## Encrypt (plain → encrypted)

1. Prompt user for a **new password** (`PasswordDialog.get_password`).
2. If this is the active workspace, close all open tabs.
3. Call `crypto.create_encrypted_workspace(folder, password)` (existing):
   - Packs the directory into tar bytes
   - Encrypts with AES-256-GCM (PBKDF2-HMAC-SHA256, 600k iterations)
   - Writes `<folder>/.subline.vault` (atomic write)
   - Securely deletes all plaintext entries (keeps only `.subline.vault`)
4. Update workspace model:
   - `ws.encrypted = True`
   - `ws.vault_path = <folder>/.subline.vault`
   - `ws.root = ""`
5. Refresh workspace list, schedule save.

---

## Decrypt (encrypted → plain)

1. Prompt user for the **password** (the one used to encrypt).
2. Resolve folder: `folder = os.path.dirname(ws.vault_path)`
3. Call `crypto.decrypt_workspace_inplace(folder, password)` (new):
   - Reads `<folder>/.subline.vault`
   - Decrypts (raises `CryptoError` on bad password / corrupt data)
   - Extracts tar into `<folder>`
   - Securely deletes the vault file
4. Update workspace model:
   - `ws.encrypted = False`
   - `ws.vault_path = None`
   - `ws.root = folder`
5. Load workspace into editor view, refresh list, schedule save.

---

## Add Files to Vault (mixed → encrypted)

Merges all plaintext files in the folder into the vault, then deletes them.

1. Prompt user for the **vault password**.
2. Call `crypto.merge_into_vault(folder, password, resolve_collision)` (new):
   - Decrypts vault to a temp dir
   - Walks all plaintext files in the folder (excluding `.subline.vault`)
   - For each file:
     - **No collision** (path not in vault) → copy into temp dir
     - **Collision** (same path in both) → call `resolve_collision(rel_path)`:
       - `"overwrite"` — plaintext replaces vault version
       - `"keep_vault"` — vault version kept, plaintext discarded
       - `"keep_both"` — plaintext renamed with suffix ` (1)`, ` (2)`, …
   - **Folders merge by union**: if both have `subdir/`, contents combine
   - Re-encrypts the merged temp dir into `<folder>/.subline.vault`
   - Securely deletes all plaintext files from the folder
   - Result: folder contains only `.subline.vault`
3. Refresh file tree + workspace list, schedule save.

---

## Collision Resolution Dialog

When a file exists at the same path in both vault and plaintext:

```
┌──────────────────────────────────────────────┐
│  "src/file.txt" exists in both vault and     │
│  plaintext. How do you want to resolve?      │
│                                              │
│  [ Overwrite with plaintext ]  ← AcceptRole  │
│  [ Keep vault version ]      ← RejectRole    │
│  [ Keep both (rename) ]    ← ActionRole      │
└──────────────────────────────────────────────┘
```

- **Overwrite**: plaintext file replaces the vault version.
- **Keep vault**: vault version retained, plaintext file is discarded.
- **Keep both**: plaintext file is renamed with a numeric suffix
  (`file (1).txt`, `file (2).txt`, …) to avoid the collision.

---

## Mixed-State File Tree View

When the workspace is in mixed state, the file tree shows **two sections**:

```
Explorer
/workspace/path
  ├── new_file.txt          ← plaintext (from folder)
  ├── another.txt           ← plaintext (from folder)
  ├── subdir/               ← plaintext (from folder)
  │     └── ...
  └── 🔒 Encrypted         ← vault contents (from temp dir)
        ├── original.txt
        ├── other.txt
        └── vsubdir/
              └── ...
```

- **Section 1** (plaintext): backed by `QFileSystemModel` on the workspace
  folder, showing all entries except `.subline.vault`.
- **Section 2** (encrypted): backed by a second `QTreeView` showing the vault
  contents (from the unlocked temp dir). Labelled "🔒 Encrypted".
- Clicking a file in either section opens it in the editor.
- The "Encrypted" section is visible when the vault is unlocked
  (auto-unlock on workspace activation, or via existing Unlock action).

---

## UI Entry Points

All actions are triggered from the **workspace list context menu** (left panel).

### Plain workspace (`encrypted=False`)

```
Rename Workspace
Copy Absolute Path
Open in File Explorer
Open Terminal Here
──────────────────
Encrypt…
──────────────────
Move Up
Move Down
Move to Category…
Close Workspace
```

### Encrypted workspace (`encrypted=True`, no plaintext files)

```
Rename Workspace
Unlock…
Lock
Change Password…
──────────────────
Decrypt…
──────────────────
Move Up
Move Down
Move to Category…
Close Workspace
```

### Mixed workspace (`encrypted=True` + plaintext files exist)

```
Rename Workspace
Unlock…
Lock
Change Password…
──────────────────
Add files to vault…
Decrypt…
──────────────────
Move Up
Move Down
Move to Category…
Close Workspace
```

### New signals (WorkspaceList)

| Signal | Emitted by |
|--------|-----------|
| `encryptRequested(str)` | "Encrypt…" (workspace id) |
| `decryptRequested(str)` | "Decrypt…" (workspace id) |
| `addFilesToVaultRequested(str)` | "Add files to vault…" (workspace id) |

---

## Password Flow

- **Encrypt**: prompt for a new password (set once at encryption time).
- **Decrypt**: prompt for the password (the one used to encrypt).
- **Add files to vault**: prompt for the vault password.
- No password is stored. Every operation requires user input.

---

## Relationship to Existing Unlock/Lock

| Feature | Mechanism | Vault file | Use case |
|---------|-----------|-----------|----------|
| **Unlock/Lock** (existing) | temp dir under `~/.local/share/subline/vaults/<id>/` | persists | Temporary access to modify encrypted files |
| **Encrypt/Decrypt** (new) | in-place in the workspace folder | created/deleted | Permanent state toggle of the workspace |

Both coexist. A user can:
- Unlock → edit files in temp dir → Lock (vault persists, workspace stays encrypted)
- Decrypt → work with plain files → Encrypt (vault re-created, workspace toggles)
- Add files to vault → absorb plaintext into vault (mixed → encrypted)

---

## Moving Files Within the Vault

No new UI needed. The existing **Unlock → move in temp dir → Lock** flow
handles intra-vault moves.

---

## File Changes Summary

| File | Change |
|------|--------|
| `texteditor/crypto.py` | Add `decrypt_workspace_inplace()`, `merge_into_vault()` |
| `texteditor/workspace_list.py` | Add 3 signals + menu items for Encrypt/Decrypt/AddFilesToVault |
| `texteditor/main_window.py` | Add `_on_encrypt`, `_on_decrypt`, `_on_add_files_to_vault`, collision dialog |
| `texteditor/file_tree.py` | Mixed-state two-section view (second tree for vault contents) |
| `texteditor/password_dialog.py` | No changes (reuse existing `PasswordDialog.get_password`) |
| `texteditor/store.py` | No changes (existing `encrypted`/`vault_path` fields suffice) |
| `tests/test_crypto.py` | Add tests for new crypto functions |

---

## Edge Cases

- Wrong password on decrypt/merge → `CryptoError`, nothing modified, vault intact
- Active workspace with open tabs → tabs closed before state change
- Folder not found / vault missing → clear error message
- Empty password → rejected
- Collision "keep both" with existing renamed files → increment suffix until unique
- Existing Unlock/Lock flow unaffected

---

## Test Cases

1. `test_decrypt_workspace_inplace_roundtrip` — encrypt folder → decrypt → verify files match
2. `test_decrypt_workspace_inplace_wrong_password` — raises `CryptoError`, vault intact
3. `test_decrypt_workspace_inplace_missing_vault` — raises `CryptoError`
4. `test_merge_into_vault_basic` — vault + new plaintext → merge → only vault remains, all files present
5. `test_merge_into_vault_collision_overwrite` — same path both → plaintext wins
6. `test_merge_into_vault_collision_keep_vault` — same path both → vault wins
7. `test_merge_into_vault_collision_keep_both` — same path both → renamed file created
8. `test_merge_into_vault_folder_merge` — both have `subdir/` → contents union
9. `test_merge_into_vault_secure_delete` — plaintext files securely deleted after merge
