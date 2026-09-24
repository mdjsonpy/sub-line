# Implementation Plan — Two-Pane Split + Image Navigation

## Goals
1. **Split is limited to once** — at most 2 panes side-by-side (a "left" group and a "right" group). No nested / multiple splits.
2. **Tab context menu** — "Move Tab to the Left" / "Move Tab to the Right" to relocate a tab between the two panes.
3. **"End Split View"** — collapses the two panes back into a single pane.
4. **Image navigation** — when an image is displayed, **Left/Right arrow keys** cycle to the previous/next image (same folder, alphabetical, wraps).
5. **File tree context menu** — "Open in the Left" / "Open at the Right" to open a file into a specific pane.

## Chosen model (confirmed)
- **Relocate, no duplication.** Tabs/files are *moved* between panes; the old "Split to the Right" (which duplicated/cloned the active pane) is **removed**.
- The right pane is **created on demand** by the first "Move Tab to the Right" or "Open at the Right" (this is the single allowed split).
- **Image navigation = switch in place**: a single image tab swaps its displayed file (title updates). No new tabs.
- **Image nav scope = same folder**, alphabetical, wraps around.

## Files to touch
- `texteditor/workspace.py` (core: groups, move, open-in-side, end-split, tab menu, image nav wiring)
- `texteditor/image_view.py` (ImagePane: arrow keys, `set_path`, signals)
- `texteditor/file_tree.py` (tree context menu: Open in the Left / at the Right + signal)
- `texteditor/main_window.py` (View menu: "End Split View")
- `texteditor/binary.py` (helper `is_image`)
- `smoke_test.py` (update the "5c-split" section to the new relocate behavior)

---

## 1. `texteditor/binary.py`
Add a cheap extension-only image check (no file reads, for folder listing):
```python
def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS
```

## 2. `texteditor/image_view.py`
### 2a. `ImagePane` — add signals + navigation
```python
from PySide6.QtCore import QEvent
```
```python
class ImagePane(QWidget):
    modificationChanged = Signal(bool)
    pathChanged = Signal()          # emitted when filepath changes (navigation)
    navigateRequested = Signal(int) # Left = -1, Right = +1
```
In `__init__`, after building `self.canvas` / `self.scroll`, install key event filters so arrows work regardless of which child holds focus:
```python
self.canvas.installEventFilter(self)
self.scroll.installEventFilter(self)
self.scroll.viewport().installEventFilter(self)
```
Add methods:
```python
def set_path(self, path: str) -> None:
    path = os.path.abspath(path)
    if path == self.filepath:
        return
    self.filepath = path
    self.canvas.load(path)          # existing method; resets scale + repaints
    self.pathChanged.emit()

def eventFilter(self, obj, event):
    if event.type() == QEvent.KeyPress:
        if event.key() == Qt.Key_Left:
            self.navigateRequested.emit(-1)
            return True
        if event.key() == Qt.Key_Right:
            self.navigateRequested.emit(1)
            return True
    return super().eventFilter(obj, event)

def keyPressEvent(self, event):
    if event.key() == Qt.Key_Left:
        self.navigateRequested.emit(-1); return
    if event.key() == Qt.Key_Right:
        self.navigateRequested.emit(1); return
    super().keyPressEvent(event)
```
### 2b. Folder-neighbor helper (module level)
```python
from .binary import is_image

def image_neighbor(path: str, step: int) -> str | None:
    """Previous/next image in the same folder (alphabetical, wraps). None if none."""
    folder = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(path)
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    images = sorted((e for e in entries if is_image(os.path.join(folder, e))),
                    key=str.lower)
    if name not in images:
        return None
    i = images.index(name)
    return os.path.join(folder, images[(i + step) % len(images)])
```

## 3. `texteditor/workspace.py`  (core)
### 3a. Enforce max-2 groups
- Add a cap helper:
```python
def left_group(self) -> QTabWidget:
    return self._groups[0]

def right_group(self) -> QTabWidget:
    return self._groups[-1]

def _ensure_right_group(self) -> QTabWidget:
    if len(self._groups) >= 2:
        return self._groups[-1]
    return self._add_group_after(self._groups[0])   # creates the one split
```
- Keep `_add_group_after` / `_add_group_at_end` / `_remove_group`.
- In `sync_from_data`, **cap** restored groups at 2: if `ws.splits` has more than 2 entries, merge the extras' tab lists into the last (right) list before building groups. (Prevents a stale 3+ pane layout from old data.)

### 3b. Move a tab between panes (relocate, no clone)
```python
def _move_pane(self, pane, target_group) -> None:
    src = self._group_of(pane)
    if src is None or src is target_group:
        return
    idx = src.indexOf(pane)
    if idx < 0:
        return
    src.removeTab(idx)                 # detach tab; widget stays alive
    target_group.addTab(pane, pane.title)   # re-parent + attach (append)
    target_group.setCurrentWidget(pane)
    self._on_pane_modified(pane, pane.isModified())  # refresh tab text
    self.tabsChanged.emit()
    if src.count() == 0 and len(self._groups) > 1:
        self._remove_group(src)        # auto-collapse an emptied pane

def move_tab(self, group, index, side) -> None:
    pane = group.widget(index)
    if pane is None:
        return
    if side == "left":
        target = self._groups[0]
    else:
        target = self._ensure_right_group()
    if target is group:
        return
    self._move_pane(pane, target)
```
> NOTE: `_move_pane` must NOT call `_add_pane_to` (that would double-connect `modificationChanged`). It re-parents directly.

### 3c. Open a file into a specific pane (from the tree)
```python
def open_in_side(self, path: str, side: str) -> None:
    path = os.path.abspath(path)
    existing = self.pane_for_path(path)
    if existing is not None:
        g = self._group_of(existing)
        if g is not None:
            g.setCurrentWidget(existing)
        return
    target = self._groups[0] if side == "left" else self._ensure_right_group()
    pane = self._make_pane_for(path)
    self._add_pane_to(target, pane)
    self.tree.selectPath(path)
```
Wire the new tree signal in `__init__`:
```python
self.tree.openInGroupRequested.connect(self.open_in_side)
```

### 3d. End Split View
```python
def end_split(self) -> None:
    if len(self._groups) <= 1:
        return
    left = self._groups[0]
    right = self._groups[-1]
    while right.count():
        pane = right.widget(0)
        right.removeTab(0)             # detach (widget survives)
        left.addTab(pane, pane.title)  # re-parent into left
    cur = left.currentWidget()
    if cur is not None:
        left.setCurrentWidget(cur)
    self._remove_group(right)
    self.tabsChanged.emit()
```

### 3e. Image navigation wiring
In `_add_pane_to`, after the existing `EditorPane` setup block, add:
```python
if isinstance(pane, ImagePane):
    pane.navigateRequested.connect(lambda step, p=pane: self._navigate_image(p, step))
    pane.pathChanged.connect(lambda p=pane: self._update_pane_title(p))
```
Add:
```python
def _navigate_image(self, pane, step) -> None:
    if not getattr(pane, "filepath", None):
        return
    neighbor = image_neighbor(pane.filepath, step)   # import from .image_view
    if neighbor:
        pane.set_path(neighbor)

def _update_pane_title(self, pane) -> None:
    group = self._group_of(pane)
    if group is None:
        return
    i = group.indexOf(pane)
    if i < 0:
        return
    group.setTabText(i, pane.title + (" ●" if pane.isModified() else ""))
```
(Import `image_neighbor` at top: `from .image_view import ImagePane, image_neighbor`.)

### 3f. Tab context menu — replace the old split option
Rewrite `show_tab_context_menu` to offer move + end-split (keep the existing close options):
```python
def show_tab_context_menu(self, group, index) -> None:
    menu = QMenu(self)
    act_move_left = menu.addAction("Move Tab to the Left")
    act_move_right = menu.addAction("Move Tab to the Right")
    act_end = menu.addAction("End Split View")
    menu.addSeparator()
    act_close = menu.addAction("Close Tab")
    menu.addSeparator()
    act_right = menu.addAction("Close to the Right")
    act_left = menu.addAction("Close to the Left")
    act_others = menu.addAction("Close Others")
    count = group.count()
    act_right.setEnabled(index + 1 < count)
    act_left.setEnabled(index > 0)
    act_others.setEnabled(count > 1)

    split = len(self._groups) >= 2
    left, right = self._groups[0], self._groups[-1]
    act_move_left.setEnabled(split and group is right)
    act_move_right.setEnabled((not split) or (split and group is left))
    act_end.setEnabled(split)

    chosen = menu.exec(QCursor.pos())
    if chosen is act_move_left:
        self.move_tab(group, index, "left")
    elif chosen is act_move_right:
        self.move_tab(group, index, "right")
    elif chosen is act_end:
        self.end_split()
    elif chosen is act_close:
        self.request_close_tab(index, group)
    elif chosen is act_right:
        self.close_tabs_to_right(group, index)
    elif chosen is act_left:
        self.close_tabs_to_left(group, index)
    elif chosen is act_others:
        self.close_others(group, index)
```

### 3g. Remove the duplicate-split path
- Delete `split_to_right`, `split_tab_at`, and `_clone_pane` (no longer used in the relocate model).
  - If you prefer a safe rollout, keep `_clone_pane` but it must no longer be reachable from any UI path.
- Make sure nothing else references them (see `smoke_test.py` below).

## 4. `texteditor/file_tree.py`
- Add signal:
```python
openInGroupRequested = Signal(str, str)   # (path, "left"|"right")
```
- In `_on_context_menu`, when `files` is non-empty, add the two actions (after "Open"):
```python
if files:
    act_open = menu.addAction("Open")
    menu.addSeparator()
    act_open_left = menu.addAction("Open in the Left")
    act_open_right = menu.addAction("Open at the Right")
```
- Handle selection:
```python
elif chosen is act_open_left:
    for p in files:
        self.openInGroupRequested.emit(p, "left")
elif chosen is act_open_right:
    for p in files:
        self.openInGroupRequested.emit(p, "right")
```

## 5. `texteditor/main_window.py`
- In the **View** menu, add "End Split View":
```python
self._act("end_split", "End Split View", self.end_split)
view_menu.addAction(self._actions["end_split"])
```
- Add the delegate:
```python
def end_split(self) -> None:
    if self.current_workspace() is None:
        return
    self._view.end_split()
    self._schedule_save()
```
> (No standalone "Split View" create action is added — the split is created via the tree "Open at the Right" or the tab "Move Tab to the Right".)

## 6. `smoke_test.py` — update "5c-split"
The old block asserts duplication (`clone is not ...`, `split group holds 1 pane`, `to_data group 1 = [f2]`). Replace it with relocate assertions, e.g.:
- `sp.move_tab(sp.tabs, 1, "right")` → 2 groups, right holds the moved tab, left lost it (no duplicate).
- `sp.move_tab(sp._groups[-1], 0, "left")` → back to 1 group (auto-collapse) **or** left re-gains it.
- `sp.end_split()` after a split → `len(sp._groups) == 1`.
- `sp.open_in_side(f2, "right")` with 1 group → creates the right group holding `f2`.
- "Move Tab to the Right" from 1 group creates the right group (single split); a 3rd group is never created.
- Image nav: `ImagePane.set_path` swaps `filepath` + canvas; `image_neighbor` returns the alphabetically adjacent file in the same folder and wraps.
- Keep the restore-from-data test but assert a **capped** 2-group result.

## Edge cases to handle / verify
- Moving a tab out of a pane empties it → that pane auto-collapses to a single pane (`_remove_group`).
- "Move Tab to the Left" is disabled when already in the left group or when only 1 pane exists.
- "Move Tab to the Right" is enabled from a single pane (creates the split) and from the left pane; disabled when already in the right pane.
- "End Split View" is a no-op when only 1 pane exists.
- Opening an already-open file "in the Right" just focuses it (no duplicate).
- `to_data`/`sync_from_data` remain consistent with a max of 2 `splits`; stale 3+ pane data is capped on load.
- Image arrows only act when the image pane has focus; they don't conflict with the existing wheel/drag pan or Ctrl+wheel zoom.

## Verification
- `QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py` → all checks pass.
- Run the pytest suite: `.venv/bin/python -m pytest -q`.
- Manual (if a display is available): create a split via tree "Open at the Right", move tabs both ways, "End Split View", and arrow-key through a folder of images.
