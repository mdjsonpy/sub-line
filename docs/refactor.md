# Subline — Refactoring Proposal

A code-quality and architecture refactoring plan for the Subline text editor.
This is a **behaviour-preserving** refactor: the goal is to make the code
easier to read, test, and extend — not to change what the app does.

- Status: proposal (not yet implemented)
- Scope: `texteditor/` package
- Estimate: ~6–10 focused sessions across 7 phases (see §10)

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Current architecture (as-is)](#2-current-architecture-as-is)
3. [Issues & risks, ranked](#3-issues--risks-ranked)
4. [Target architecture (to-be)](#4-target-architecture-to-be)
5. [Phased refactoring plan](#5-phased-refactoring-plan)
6. [Key refactors — before / after code](#6-key-refactors--before--after-code)
7. [What we deliberately do NOT change](#7-what-we-deliberately-do-not-change)
8. [Testing & verification strategy](#8-testing--verification-strategy)
9. [Risks & mitigations](#9-risks--mitigations)
10. [Order, effort & dependencies](#10-order-effort--dependencies)
11. [Open questions / decisions needed](#11-open-questions--decisions-needed)

---

## 1. Executive summary

Subline is a well-structured PySide6 application. The low-level modules
(`crypto.py`, `vault.py`, `binary.py`, `store.py`, `todos.py`, `markdown.py`)
are clean, focused, and mostly single-responsibility. The feature set is broad
and genuinely useful: encrypted workspaces with crash recovery, split views,
markdown table formatting, image preview with navigation, and a todos board.

The problems are concentrated in the **orchestration layer**:

- **`WorkspaceView`** (`workspace_view.py`, ~1200 lines) is a *God object*: it
  owns the file tree, the tab bar, the split-view tree, every open pane,
  search, image navigation, and save/dirty tracking — and it is the only place
  that knows how to turn a file path into the right pane.
- **`MainWindow`** (`main_window.py`, ~1160 lines) is a second God object that
  mixes menus, workspace lifecycle, **all** encryption/vault orchestration,
  todos wiring, and persistence.
- The **pane contract** (`title`, `filepath`, `isModified`, `save`, `saveAs`,
  `modificationChanged`) is an *implicit* duck-typed agreement between four
  unrelated classes. Nothing documents or enforces it, so new panes are added
  by copying conventions.
- Several **monolithic methods** (`WorkspaceView.open_file`,
  `MainWindow._load_workspaces`, `MainWindow._build_menus`,
  `FileTree.__init__`) each do 5–8 unrelated jobs.
- There is **no automated test suite** (`tests/` is empty), so any refactor
  currently rests on manual verification.

The proposed refactor introduces an explicit **Pane protocol + factory**,
**decomposes the two God objects** by extracting services (`PaneFactory`,
`VaultService`, menu builders), **removes the feature-flag coupling** baked
into `open_file`, and lays the groundwork for a **smoke test harness**.

The highest-value, lowest-risk work is **Phase 1 (explicit pane protocol +
factory)** and **Phase 2 (decompose `open_file`)** — together they remove the
most-copied convention and the single biggest monolith, with minimal risk.

> **What this is NOT:** a rewrite, a framework swap, a feature add, or a UI
> redesign. It does not touch the crypto/vault *algorithm*, the markdown
> parser, the todos data model, or the public behaviour of any dialog.

---

## 2. Current architecture (as-is)

### 2.1 Module map

| File | Responsibility | ~Lines | Note |
|---|---|---:|---|
| `__init__.py` | Entry point, app bootstrap, theme, CLI args, drag-drop | 95 | Fine |
| `main_window.py` | Menu bar, workspace list, **encryption orchestration**, todos wiring, persistence | 1160 | **God object** |
| `workspace_view.py` | Tree + tabs + split tree + all panes + search + image nav + save | 1199 | **God object** |
| `file_tree.py` | Folder sidebar (create/rename/delete/move/copy-path) | 449 | Large `__init__` |
| `editor_view.py` | Text pane + syntax highlighting | 598 | OK |
| `find_bar.py` | Find/replace bar | 253 | OK |
| `todos_view.py` | Todos board view | ~450 | OK |
| `image_view.py` | Image preview pane (zoom, GIF, nav) | 362 | OK |
| `binary_stub.py` | Placeholder for video/audio/binary | ~40 | OK |
| `todos_pane.py` | Todos pane (board + pane contract) | 78 | OK |
| `todos.py` | Todos data model + store | 180 | OK |
| `crypto.py` | AES-GCM encryption core | 274 | Clean |
| `vault.py` | Vault lifecycle (lock/unlock/recover) | 287 | Clean |
| `binary.py` | File classification (text/image/video/audio) | 106 | Clean |
| `store.py` | Workspace/layout/settings persistence (JSON) | 171 | Clean |
| `markdown.py` | Table formatting | 86 | Clean |
| `theme.py` | QSS theme + palette constants | 421 | OK |
| `password_dialog.py` | Unlock / change-password dialogs | 84 | OK |
| `new_workspace_dialog.py` | New-workspace dialog | 113 | OK |

### 2.2 The implicit pane contract

Every tab's content is a *pane*. Four classes independently implement the same
shape, by convention only:

| Member | `EditorPane` | `ImagePane` | `BinaryPane` | `TodosPane` |
|---|:--:|:--:|:--:|:--:|
| `title` (property) | ✓ | ✓ | ✓ | ✓ |
| `filepath` (attr) | ✓ | ✓ | ✓ | ✓ |
| `isModified()` | ✓ | ✓ (False) | ✓ (False) | ✓ |
| `save()` | ✓ | ✓ (False) | ✓ (False) | ✓ |
| `saveAs(path)` | ✓ | ✓ (False) | ✓ (False) | ✓ |
| `modificationChanged: Signal[bool]` | ✓ | ✓ | ✓ | ✓ |
| `kind` (attr) | `"text"` | `KIND_IMAGE` | `KIND_VIDEO/AUDIO/BINARY` | — (uses `isinstance`) |
| edit verbs (`undo/redo/cut/copy/paste/selectAll`) | ✓ | — | — | — |
| `format_table` / `format_all_tables` | ✓ | — | — | — |
| `zoomIn/zoomOut/resetZoom`, `isFallback()` | — | ✓ | — | — |
| `navigateRequested: Signal[int]`, `set_path()` | — | ✓ | — | — |
| `load(path)`, `changed: Signal` | — | — | — | ✓ |

`WorkspaceView` and `MainWindow` both rely on this shape via attribute access
and `isinstance` checks scattered across ~10 call sites. **Nothing in the code
declares the contract.** This is the single most important thing to make
explicit.

### 2.3 Object graph (as-is)

```
                    ┌──────────────────────────────┐
   __init__.py ───▶ │            MainWindow        │  (menus, lifecycle,
                    │  • _workspaces: [Workspace]  │   vault orchestration,
                    │  • _layout: [LayoutEntry]    │   todos, persistence)
                    │  • _vaults:  VaultManager    │
                    └───────┬──────────────┬───────┘
              _view (1)     │              │  _ws_list (1)
                            ▼              ▼
                  ┌──────────────────┐  ┌─────────────────┐
                  │   WorkspaceView  │  │   WorkspaceList  │  (sidebar list)
                  │  • _tree (1)     │  └─────────────────┘
                  │  • _tabbar (1)   │
                  │  • _split (1)    │
                  │  • _panes: [] ◀──┼────────────────────────────┐
                  │  • _split_panes  │                             │
                  │  • _find_bar (1) │        ┌────────────────────┼─────────────┐
                  └───┬──────────┬───┘        │   Pane (duck-typed)             │
                      │          │            ├─ EditorPane                      │
                  ┌────▼───┐ ┌───▼────┐       ├─ ImagePane                       │
                  │FileTree│ │EditorPane…      ├─ BinaryPane                      │
                  │(QTree) │ └────────┘       └─ TodosPane                       │
                  └────────┘               (created ad-hoc in open_file/new_*)  ◀┘
```

Key coupling edges to relax:
- `WorkspaceView` → `FileTree` (direct method calls: `set_vault_root`,
  `clear_vault_root`, plus `_on_tree_activated` reading tree state)
- `WorkspaceView` → concrete pane classes (`EditorPane`, `ImagePane`,
  `BinaryPane`) in `open_file` / `_open_image` / `_open_binary`
- `MainWindow` → `crypto` + `vault` (direct crypto calls in 6 methods)
- `MainWindow` → `EditorPane` (type checks in `_active_pane` / `_delegate`)

---

## 3. Issues & risks, ranked

Severity: **H**igh / **M**edium / **L**ow. "Blast radius" = how much code a
future change would have to touch.

### 3.1 God objects — `WorkspaceView` & `MainWindow` — **H**
Both exceed ~1100 lines and each owns 6+ responsibilities. Consequences:
- Hard to test in isolation (constructing either pulls in Qt widgets, the
  file system, and crypto).
- Every new feature (e.g. a video pane) requires editing both.
- Method bloat: `WorkspaceView` has ~45 public methods; `MainWindow` ~60.

### 3.2 Implicit pane contract (duck typing) — **H**
No protocol, no factory, no single dispatch point. Adding a pane means
knowing 4 files to touch and 6 members to implement. `isinstance(pane,
EditorPane)` checks are scattered (e.g. `main_window.py:1055`,
`main_window.py:855`). The `kind` attribute is set inconsistently (todos pane
has none).

### 3.3 Feature-flag coupling in `open_file` — **M**
`workspace_view.py:120-136` both *creates* the pane and *wires* image-only
navigation:
```python
if kind == KIND_IMAGE:
    self._open_image(path)          # create + connect navigateRequested
    for pane in self._panes:        # and re-attach nav to OTHER image panes
        if isinstance(pane, ImagePane):
            pane.navigateRequested.connect(...)
```
The "re-attach to all existing image panes" logic is an invisible invariant:
every image pane must share the same navigation closure, or arrow-key
navigation breaks. This is exactly the kind of coupling that fails silently.

### 3.4 Monolithic methods — **M**
- `MainWindow._build_menus` (`main_window.py:107-210`, ~100 lines): builds 6
  menus inline. Adding a menu item means navigating a 100-line method.
- `MainWindow._load_workspaces` (`main_window.py:235-276`): loads store,
  restores settings, restores active ws, restores window size, refreshes list —
  5 concerns in one method.
- `FileTree.__init__` (`file_tree.py:50-171`, ~120 lines): builds layout,
  header, buttons, model, filters, proxy, and connects 10 signals.
- `WorkspaceView._on_tree_activated` (`workspace_view.py:636-670`): 5
  different file actions behind one signal.

### 3.5 Tight coupling `WorkspaceView` ↔ `FileTree` — **M**
`WorkspaceView` calls `tree.set_vault_root(...)`, `tree.clear_vault_root(...)`,
`tree.rootPath()`, `tree.model()`, `tree._proxy` (private attr at
`workspace_view.py:610`). The tree's vault section is a feature only the view
knows about.

### 3.6 Tight coupling `MainWindow` → crypto/vault — **M**
`main_window.py` directly calls `crypto.create_encrypted_workspace`,
`crypto.folder_has_plaintext`, `crypto.decrypt_workspace_inplace`,
`crypto.merge_into_vault`, and drives `VaultManager` in 9 methods
(`_on_encrypt`, `_on_decrypt`, `_on_add_files_to_vault`, `_ensure_unlocked`,
`_lock_workspace`, `_on_change_password`, `_recover_vaults`, …). The
*policy* (when to prompt, when to confirm) is fused with the *mechanism*
(crypto calls), so neither can be reasoned about or tested alone.

### 3.7 Duplication — **M**
- Save/saveAs "confirm → save → set_modified(False) → emit" pattern repeated in
  every pane and in `WorkspaceView._save_pane`.
- The `KIND_*` constants live in `binary.py` but are re-imported/referenced in
  4+ modules; `EditorPane` hard-codes the string `"text"`.
- `zoomIn/zoomOut` in `MainWindow` (`main_window.py:1089-1105`) are 3
  near-identical methods delegating to the view.
- Settings capture (`_current_settings`, `main_window.py:1115-1121`) and
  restore (`_load_workspaces:244-250`) are mirror images of each other.

### 3.8 No automated tests — **H** (process risk)
`tests/` exists but is empty. `pyproject.toml` has no test config. Any refactor
is currently verified by hand. This is the biggest *enabler* gap: a small
smoke-test harness would let every phase be proven safe.

### 3.9 Naming & consistency — **L**
- Mixed method casing: `isModified`, `saveAs`, `selectAll` (CamelCase) vs
  `set_modified`, `set_path`, `load` (snake_case). Pick one (recommend
  snake_case for the protocol, keep Qt-cased only where Qt requires it).
- `_active_pane` exists in **both** `MainWindow` (returns `EditorPane`) and
  `WorkspaceView` (returns any pane) — different meanings, same name.

---

## 4. Target architecture (to-be)

The core moves:

1. **Make the pane contract explicit.** A `Pane` `Protocol` (+ capability
   protocols) documents the shape; a `PaneFactory` is the *single* place that
   maps a path → pane. All `isinstance` dispatch disappears.
2. **Decompose `WorkspaceView`** into a thin controller plus a *pane manager*
   (owns `_panes`, dirty tracking, save, clone, split assignment). Image
   navigation moves to a small `ImageNavigator` collaborator.
3. **Extract vault orchestration** from `MainWindow` into a `VaultService`
   that talks to the UI only through narrow hooks (confirm / close-tabs /
   load / refresh) — making it testable with fakes.
4. **Split the monolithic methods** (menu builders, load, tree init) into
   small, named, single-purpose methods.
5. **Add a smoke-test harness** so each phase is verifiable.

### 4.1 Module map (to-be)

| New / changed | Purpose |
|---|---|
| **NEW** `pane.py` | `Pane` protocol + capability protocols (`Editable`, `Markdown`, `Zoomable`) |
| **NEW** `panes.py` | `make_pane(path) -> Pane` factory (single dispatch point) |
| **NEW** `vault_service.py` | All encryption orchestration, UI-agnostic (hook-based) |
| **NEW** `tests/` | Smoke tests: factory, pane protocol, vault service, store round-trip |
| `workspace_view.py` | Shrinks: delegates pane creation to factory; pane lifecycle moves to `PaneManager` |
| **NEW** `pane_manager.py` | Owns `_panes`, dirty tracking, save, clone, split assignment |
| `main_window.py` | Shrinks: vault methods → `VaultService`; menus → per-menu builders |
| `file_tree.py` | `__init__` split into `_build_*` helpers; vault section behind an interface |
| `editor_view.py`, `image_view.py`, `binary_stub.py`, `todos_pane.py` | Adopt the `Pane` protocol (mostly already conformant); consistent naming |

### 4.2 Object graph (to-be)

```
MainWindow  ──(owns)──▶  VaultService          (encryption policy+mechanism)
   │  └──(owns)──▶      MenuBuilder(s)         (per-menu construction)
   │
   └──▶ WorkspaceView ──▶ PaneManager          (panes, dirty, save, split)
              │              │
              │              └── make_pane(path)  ──▶ EditorPane / ImagePane / BinaryPane
              │                                      (via PaneFactory, single point)
              └──▶ FileTree  ◀──(event: activated(path))──▶ WorkspaceView
              │
              └──▶ ImageNavigator (optional, only when an image pane is open)
```

Coupling now flows **downward** (view → manager → factory → pane) and the
tree communicates **via a signal** rather than the view poking its internals.

---

## 5. Phased refactoring plan

Each phase is **self-contained**, **behaviour-preserving**, and **verifiable**
on its own. Do them in order; each reduces the risk of the next.

### Phase 0 — Safety net (enablement)
- **Goal:** make refactoring provable.
- Add `pytest` + `pytest-qt` (or a minimal offscreen Qt harness) to
  `pyproject.toml`; add `tests/test_factory.py`, `tests/test_pane_protocol.py`,
  `tests/test_store_roundtrip.py`, `tests/test_vault_service.py`.
- Add a `make test` / `npm`-free script target.
- **Risk:** low. **Value:** unblocks everything else.

### Phase 1 — Explicit pane protocol + factory (**highest value, lowest risk**)
- **Goal:** replace the implicit contract with a declared one and a single
  dispatch point.
- Add `pane.py` (protocols) and `panes.py` (`make_pane`).
- Point `WorkspaceView.open_file`, `new_untitled`, `_open_image`,
  `_open_binary` at the factory.
- Replace `isinstance(pane, EditorPane)` in `MainWindow` with capability
  checks (`hasattr`) or the `Editable` protocol.
- Give `TodosPane` a `kind = "todos"` for uniformity.
- **Risk:** low (pure extraction, no behaviour change). **Value:** high.

### Phase 2 — Decompose `WorkspaceView.open_file` & extract image navigation
- **Goal:** kill the feature-flag coupling (§3.3) and the "re-attach to all
  image panes" invisible invariant.
- `open_file` becomes: dedup → `make_pane` → `_add_pane` → (if image) wire nav.
- Move the re-attach loop into `PaneManager` or an `ImageNavigator` that
  subscribes once.
- **Risk:** low–medium. **Value:** high (removes the most fragile invariant).

### Phase 3 — Extract `VaultService`
- **Goal:** separate vault *policy* from `MainWindow` and make it testable.
- Move `_vault_for`, `_ensure_unlocked`, `_lock_workspace`, `_on_unlock`,
  `_on_lock`, `_on_change_password`, `_on_encrypt`, `_on_decrypt`,
  `_on_add_files_to_vault`, `_recover_vaults`, `_resolve_collision` into
  `vault_service.py`, driven by injected hooks (`confirm`, `close_tabs`,
  `load`, `refresh`, `schedule_save`, `parent`).
- `MainWindow` keeps only the menu/shortcut → `service.…()` calls.
- **Risk:** medium (most logic moved). **Value:** high (biggest God-object
  reduction; enables vault unit tests).

### Phase 4 — Split menu construction & load
- **Goal:** readability.
- `_build_menus` → `_build_file_menu` / `_build_edit_menu` / … each returning a
  `QMenu`.
- `_load_workspaces` → `_restore_settings`, `_restore_active_workspace`,
  `_restore_window`, `_refresh_list`.
- **Risk:** low. **Value:** medium.

### Phase 5 — Relax `WorkspaceView` ↔ `FileTree` coupling
- **Goal:** stop the view from reaching into tree internals.
- Replace `tree._proxy` access with a public `FileTree.selected_path()` /
  `FileTree.parent_of(path)`.
- Move the vault-section show/hide decision to a small `TreeSections` helper or
  a `vaultRootChanged` signal the view subscribes to.
- **Risk:** medium. **Value:** medium.

### Phase 6 — Consistency & small wins
- Unify method naming on the protocol (snake_case), keep Qt-cased only where
  required.
- Consolidate `KIND_TEXT` usage (replace the `"text"` literal in
  `EditorPane` with the constant).
- Collapse the three `zoom*` methods; collapse save/saveAs duplication into a
  small `_commit_save(pane)` helper.
- Rename `MainWindow._active_pane` vs `WorkspaceView._active_pane` to distinct
  names (e.g. `MainWindow._active_editor_pane`).
- **Risk:** low. **Value:** low–medium.

### Phase 7 (optional) — CI
- GitHub Actions / local pre-commit running the smoke suite on every change.
- **Risk:** low. **Value:** medium (long-term).

---

## 6. Key refactors — before / after code

### 6.1 The pane protocol (`pane.py`, NEW)
```python
"""The pane contract shared by every tab content widget.

A *pane* is the content of one tab. It is a behavioural protocol (duck-typed)
so panes stay lightweight and independent; declaring it here documents the
contract in ONE place and lets the rest of the code depend on the abstraction
instead of concrete classes.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class Pane(Protocol):
    title: str                                   # tab label
    filepath: str | None                          # None for untitled
    def isModified(self) -> bool: ...
    def save(self) -> bool: ...
    def saveAs(self, path: str) -> bool: ...
    # modificationChanged: Signal[bool]  (a Qt signal; not expressible in a Protocol)


@runtime_checkable
class Editable(Protocol):                        # text-like panes
    def undo(self) -> None: ...
    def redo(self) -> None: ...
    def cut(self) -> None: ...
    def copy(self) -> None: ...
    def paste(self) -> None: ...
    def selectAll(self) -> None: ...


@runtime_checkable
class Markdown(Protocol):
    def format_table(self) -> None: ...
    def format_all_tables(self) -> None: ...


@runtime_checkable
class Zoomable(Protocol):                        # image panes
    def zoomIn(self) -> None: ...
    def zoomOut(self) -> None: ...
    def resetZoom(self) -> None: ...
```

### 6.2 The factory (`panes.py`, NEW)
```python
"""Single dispatch point: file path -> the correct pane instance."""
from __future__ import annotations
from .binary import classify_file, KIND_IMAGE, KIND_TEXT
from .editor_view import EditorPane
from .image_view import ImagePane
from .binary_stub import BinaryPane


def make_pane(path: str) -> "Pane":
    kind = classify_file(path)
    if kind == KIND_IMAGE:
        return ImagePane(path)
    if kind == KIND_TEXT:
        return EditorPane(path)
    return BinaryPane(path, kind)
```

### 6.3 `open_file` — before / after
**Before** (`workspace_view.py:120-136`):
```python
def open_file(self, path: str) -> None:
    path = os.path.abspath(path)
    for pane in self._panes:
        if pane.filepath == path:
            self.focus_pane(pane)
            return
    kind = classify_file(path)
    if kind == KIND_IMAGE:
        self._open_image(path)
    elif kind == KIND_TEXT:
        self._add_pane(EditorPane(path))
    else:
        self._open_binary(path, kind)
```
**After:**
```python
def open_file(self, path: str) -> None:
    path = os.path.abspath(path)
    for pane in self._panes:
        if pane.filepath == path:
            self.focus_pane(pane)
            return
    pane = make_pane(path)                 # single dispatch point (panes.py)
    self._add_pane(pane)
    if isinstance(pane, ImagePane):        # image-only extras live with the image
        self._wire_image_navigation(pane)
```
…and the fragile "re-attach to all image panes" loop collapses into:
```python
def _wire_image_navigation(self, pane: ImagePane) -> None:
    pane.navigateRequested.connect(lambda step, p=pane: self._navigate_image(p, step))

def _navigate_image(self, pane: ImagePane, step: int) -> None:
    nxt = image_neighbor(pane.filepath, step)
    if nxt:
        pane.set_path(nxt)
```

### 6.4 Capability dispatch instead of `isinstance` (`main_window.py:1051-1060`)
**Before:**
```python
def _active_pane(self):
    if self.current_workspace() is None:
        return None
    pane = self._view.active_pane()
    return pane if isinstance(pane, EditorPane) else None

def _delegate(self, method: str) -> None:
    pane = self._active_pane()
    if pane is not None:
        getattr(pane, method)()
```
**After** (capability-based; drops the `EditorPane` import dependency):
```python
def _delegate(self, method: str) -> None:
    pane = self._view.active_pane()
    if pane is not None and hasattr(pane, method):   # Editable / Markdown capability
        getattr(pane, method)()
```

### 6.5 `VaultService` (`vault_service.py`, NEW) — skeleton
```python
"""Workspace encryption orchestration, decoupled from the UI.

The main window decides *what* to do (menu/shortcut); this service performs
the vault/crypto work and reports the outcome. It talks to the UI only through
narrow hooks, so it can be unit-tested with fakes.
"""
from __future__ import annotations
from typing import Callable
from . import crypto
from .vault import VaultManager, VaultError
from .store import Workspace


class VaultService:
    def __init__(self, parent, view, *,
                 refresh: Callable[[], None],
                 schedule_save: Callable[[], None]):
        self._parent = parent
        self._view = view
        self._refresh = refresh
        self._schedule_save = schedule_save
        self._vaults = VaultManager()

    # --- moved verbatim from MainWindow (bodies unchanged) ----------------------
    def vault_for(self, ws: Workspace): ...          # was _vault_for
    def ensure_unlocked(self, ws: Workspace) -> bool: ...   # was _ensure_unlocked
    def lock(self, ws: Workspace) -> bool: ...        # was _lock_workspace
    def unlock(self, ws_id: str) -> None: ...         # was _on_unlock
    def change_password(self, ws_id: str) -> None: ...# was _on_change_password
    def encrypt(self, ws_id: str) -> None: ...        # was _on_encrypt
    def decrypt(self, ws_id: str) -> None: ...        # was _on_decrypt
    def add_files_to_vault(self, ws_id: str) -> None: ...  # was _on_add_files_to_vault
    def recover(self, workspaces: list[Workspace]) -> None: ...  # was _recover_vaults
```
`MainWindow` then reduces to thin wiring, e.g.:
```python
self._ws_list.encryptRequested.connect(self._vault.encrypt)
self._ws_list.decryptRequested.connect(self._vault.decrypt)
# … and `activate_workspace` calls self._vault.ensure_unlocked(ws)
```

### 6.6 Menu builders (`main_window.py:107-210`)
**Before:** one ~100-line `_build_menus`.
**After:**
```python
def _build_menus(self) -> None:
    mb = self.menuBar()
    for builder in (self._file_menu, self._edit_menu, self._markdown_menu,
                    self._view_menu, self._todos_menu, self._help_menu):
        mb.addMenu(builder())
```
Each `self._*_menu()` is a 10–25 line method returning a `QMenu`.

---

## 7. What we deliberately do NOT change

- **No rewrite / no framework swap.** Stays PySide6, same module-per-feature
  pattern, same packaging (`pyproject.toml`, `build.py` untouched).
- **No behaviour change.** Every dialog, shortcut, and feature keeps working
  identically. This is a refactor, not a feature add.
- **Crypto/vault algorithm untouched** (`crypto.py`, `vault.py` stay as-is);
  we only move the *orchestration* around them.
- **Markdown parser & todos model untouched.**
- **No database / schema migration** (persistence stays JSON in `store.py`).
- **No new hard dependencies** beyond a dev-only test runner.

---

## 8. Testing & verification strategy

Because the app is UI-heavy, the strategy is a **smoke + unit** mix, not full
GUI automation.

**Automated (offscreen Qt, `QT_QPA_PLATFORM=offscreen`):**
- `test_pane_protocol.py` — every pane class satisfies `Pane` (`isinstance`
  via `runtime_checkable`); `make_pane` returns the right type per sample file.
- `test_store_roundtrip.py` — save/load `StoreData` round-trips; default
  settings/window applied when file is absent.
- `test_vault_service.py` — with a fake `view` + temp dirs: `encrypt` sets
  `ws.encrypted` + `vault_path`; `ensure_unlocked` prompts then unlocks;
  `recover` re-encrypts a leftover temp dir. (This is the first time vault
  logic is unit-testable — the payoff of Phase 3.)
- `test_binary.py` — `classify_file` over extension + magic-byte samples.

**Manual regression checklist (run after each phase):**
1. Plain workspace: open a `.txt`, edit, save, close/reopen → content + tab
   restored.
2. Split view: split a file, resize, close one side → other survives; state
   restored on relaunch.
3. Encrypted workspace: create → lock → unlock (correct + wrong password) →
   change password → decrypt.
4. Mixed workspace: folder with plaintext + vault; "Add files to vault";
   collision dialog (overwrite / keep vault / keep both).
5. Crash recovery: kill app while an encrypted workspace is unlocked → relaunch
   → temp dir re-encrypted, no plaintext leaked.
6. Image: open a PNG, open a GIF (animates), arrow-key prev/next, Ctrl+wheel
   zoom anchored at cursor.
7. Todos: new tab, add/done/collapse, save/load `.todo`.
8. Markdown: `format_table` under cursor; `format_all_tables`.
9. Workspaces list: categories, move up/down, move to category, rename,
   delete (closes members, keeps files).
10. Persistence: window size + split ratio + sidebar + wrap + zoom all
    restored on relaunch.

---

## 9. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Vault refactor changes lock/unlock behaviour | Medium | High | Move bodies **verbatim** first (pure relocation), verify with §8.3–8.5 *before* any signature change; add `test_vault_service.py` |
| Pane factory misses a classification edge | Low | Medium | `make_pane` calls the *existing* `classify_file` (unchanged); `test_pane_protocol.py` covers each kind |
| Image navigation regression (arrow keys) | Medium | Medium | Keep the single `navigateRequested` → `image_neighbor` path; §8.6 manual check after Phase 2 |
| Split-view state lost during pane-manager extraction | Medium | High | `PaneManager` keeps the exact `_split_panes`/`_split_actives` logic; §8.2 check after Phase 2 |
| Capability dispatch (`hasattr`) too permissive | Low | Low | Constrain with `Editable`/`Markdown` runtime-checkable protocols where precision matters |
| Refactor without tests introduces silent regressions | High (if Phase 0 skipped) | High | **Do Phase 0 first**; gate each phase on the manual checklist |
| Scope creep into features | Medium | Medium | §7 "do not change" list is the guardrail; each PR is one phase |

---

## 10. Order, effort & dependencies

| Phase | Description | Effort | Value | Risk | Depends on |
|---|---|---|---|---|---|
| 0 | Test harness + smoke tests | S | Enabler | Low | — |
| 1 | Pane protocol + factory | S | **High** | Low | 0 |
| 2 | Decompose `open_file` + image nav | M | **High** | Low–Med | 1 |
| 3 | Extract `VaultService` | M–L | **High** | Med | 0 |
| 4 | Split menu builders + load | S | Med | Low | — |
| 5 | Relax view↔tree coupling | M | Med | Med | 1 |
| 6 | Consistency & small wins | S | Low–Med | Low | 1 |
| 7 | CI | S | Med (long-term) | Low | 0 |

**Recommended sequence:** `0 → 1 → 2 → 3 → 4 → 6 → 5 → 7`.
Phases 1+2 together remove the most-copied convention and the biggest
monolith for the least risk, and should be the first milestone shipped.
Phase 3 is the largest single win (and the first to make vault logic
testable) and is best done once Phase 0's harness exists.

---

## 11. Open questions / decisions needed

1. **Test runner:** `pytest` + `pytest-qt` (offscreen) is the default — OK to
   add as a dev dependency? (Alternative: plain `unittest` + offscreen Qt.)
2. **Naming convention:** move the pane protocol to snake_case
   (`is_modified`, `save_as`, `select_all`) and keep CamelCase only where Qt
   forces it? This touches `editor_view.py` / `todos_pane.py` public methods.
3. **How far to push the protocol:** if media playback (video/audio) is the
   next feature, we may want a `MediaPane` capability protocol now so
   `BinaryPane` (the current placeholder) can be replaced without another
   refactor. Worth deciding before Phase 1.
4. **Dependency injection:** keep the lightweight "hooks as callables" approach
   for `VaultService` (proposed), or introduce a small DI container? The
   former is simpler and sufficient at this scale.
5. **Theme registry:** `theme.py` is fine for one theme. If multiple themes are
   planned, that's a separate (small) refactor — out of scope here, flagging
   only.
