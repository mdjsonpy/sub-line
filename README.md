# Sub-line

A file explorer, text editor, and image viewer for Linux, built with Python
and PySide6. Organize your work into multiple workspaces, split your view,
preview images, manage todos — and keep sensitive projects inside an
**AES-256-GCM encrypted vault**.

## Features

- **Workspaces** — manage multiple projects side by side, with categories,
  reordering, rename, and persistent layout (window size, split ratio,
  sidebar, zoom, wrap all restored on relaunch).
- **File tree** — folder sidebar with drag-and-drop move/copy, new file/folder
  creation, copy path, delete, and "open in file explorer" / "open terminal"
  context-menu actions.
- **Quick Open** — fuzzy file finder (Ctrl+P).
- **Split views** — open the same or different files in parallel panes and
  resize freely.
- **Syntax highlighting** via Pygments, with automatic encoding detection
  (chardet).
- **Find & replace** bar, undo/redo, and standard editing shortcuts.
- **Go to Line** — jump to a specific line (Ctrl+G).
- **Encrypted workspaces** (see [Encryption](#encryption)):
  - *Lock / Unlock* — while unlocked, contents live **entirely in process
    memory**; no decrypted bytes are ever written to disk. Locking
    re-encrypts the in-memory contents back into the vault file.
  - *Encrypt / Decrypt* — permanently toggle a folder between plaintext and a
    single `.subline.vault` file.
  - *Add files to vault* — merge loose files into the vault (mixed state),
    with per-collision choices: overwrite, keep vault version, or keep both.
- **Image preview** — zoom, animated GIFs, and arrow-key navigation between
  neighboring images.
- **Todos board** — a kanban-style pane backed by `.todo` files.
- **Markdown tables** — format the table under the cursor or all tables in the
  buffer.
- **Dark theme** with a cohesive QSS stylesheet.
- Ships as a single portable **AppImage** — no installation required.

## Requirements

- Python 3.10+
- Dependencies (see `requirements.txt`):
  - `PySide6` — GUI toolkit
  - `Pygments` — syntax highlighting
  - `chardet` — encoding detection
  - `cryptography` — AES-GCM vault encryption

## Getting started

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py [files-or-folders ...]
```

Any existing file or folder paths passed on the command line are opened at
startup.

### Running the tests

```bash
.venv/bin/pip install pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

A headless end-to-end smoke test is also available:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py
```

## Building the AppImage

```bash
./build_appimage.sh
```

The script creates an isolated build venv, bundles with PyInstaller
(`--onedir`), prepares the AppDir (desktop entry + icon), runs linuxdeploy,
and packages the result with appimagetool. The linuxdeploy binary and the
AppImage type-2 runtime are cached locally, so the build works offline.
Output: `Sub-line-x86_64.AppImage` — just run it.

## Encryption

Vault files use authenticated encryption with no password stored anywhere:

- Key derivation: **PBKDF2-HMAC-SHA256**, 600,000 iterations, 16-byte salt
- Cipher: **AES-256-GCM** (12-byte nonce, 16-byte tag)
- Payload: a tar archive of the workspace, packed in a fixed-size binary
  header (`SLVC` magic | salt | nonce | tag | ciphertext)
- Plaintext is **securely deleted** after encryption; a wrong password yields
  a clean `CryptoError` with the vault left intact

Every operation (unlock, encrypt, decrypt, merge) prompts for a password and
nothing is ever persisted.

### Workspace states

| State | Folder contents | How it arises |
|-------|-----------------|---------------|
| Plain | plaintext files | default |
| Encrypted | `.subline.vault` only | after *Encrypt…* or *Add files to vault…* |
| Mixed | vault + plaintext files | after *Decrypt…* with new files, or files created next to the vault |

## Project layout

```
run.py                  # launcher: python run.py
texteditor/             # the application package
  app.py                # bootstrap: QApplication, theme, main window
  main_window.py        # menus, workspace lifecycle, vault wiring
  workspace.py          # WorkspaceView: tree, tabs, split, panes
  pane.py / panes.py    # pane protocol + factory (path -> pane)
  vault_service.py      # encryption orchestration (UI-agnostic)
  editor_view.py        # text pane + syntax highlighting
  image_view.py         # image preview pane
  binary_stub.py        # placeholder for video/audio/binary
  todos_pane.py, todos_view.py, todos.py   # todos board
  file_tree.py          # folder sidebar
  find_bar.py           # find & replace
  quick_open.py         # fuzzy file finder (Ctrl+P)
  goto_line.py          # go to line dialog (Ctrl+G)
  status_bar.py         # status bar
  shortcuts_help.py     # keyboard shortcuts reference
  crypto.py             # AES-GCM vault primitives (no Qt)
  vault.py              # vault lock/unlock lifecycle + crash recovery
  store.py              # workspace/layout/settings persistence (JSON)
  binary.py             # file classification (text/image/video/audio)
  theme.py              # QSS theme
  password_dialog.py    # password prompts
  highlighter.py        # Pygments -> QSyntaxHighlighter
  markdown_tables.py    # table formatting
  md_table_highlighter.py  # markdown table highlighting
  memory_tree_model.py  # in-memory tree model
  workspace_fs.py, workspace_list.py, new_workspace_dialog.py
tests/                  # pytest suite (crypto, vault, store, tree, ...)
smoke_test.py           # headless end-to-end smoke test
specs.md                # feature specification (encrypt/decrypt workspace)
docs/refactor.md        # architecture & refactoring notes
build_appimage.sh       # AppImage build pipeline
icons/                  # application icons
```

## Notes

- Workspace list, layout, and settings are persisted as JSON at
  `$XDG_CONFIG_HOME/sub-line/workspaces.json` (defaults to
  `~/.config/sub-line/workspaces.json`).
- Encrypted vaults are stored as a single `.subline.vault` file inside the
  workspace folder itself.
