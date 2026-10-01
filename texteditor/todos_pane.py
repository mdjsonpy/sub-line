"""Todos tab pane: a :class:`TodosView` board wrapped in the pane contract
(title, filepath, isModified, save, saveAs) that WorkspaceView tabs follow."""
from __future__ import annotations

import os

from PySide6.QtCore import Signal

from .todos import Todos, todos_from_dict, todos_to_dict
from .todos_view import TodosView


class TodosPane(TodosView):
    """A todos board living in a workspace tab.

    Adds the standard pane interface on top of the board view so tab
    management (dirty tracking, save, save-as, clone) works unchanged.
    """

    kind = "todos"

    modificationChanged = Signal(bool)

    def __init__(self, parent=None, fs=None) -> None:
        super().__init__(parent, fs=fs)
        self.filepath: str | None = None
        self._modified = False
        self._loading = False
        self.changed.connect(self._on_changed)

    # -- pane contract --------------------------------------------------------
    @property
    def title(self) -> str:
        return os.path.basename(self.filepath) if self.filepath else "Todos"

    def isModified(self) -> bool:  # noqa: N802
        return self._modified

    def load(self, path: str) -> bool:
        """Load a .todo file, tracking the path and resetting the dirty flag."""
        self._loading = True
        try:
            ok = self.load_from_file(path)
        finally:
            self._loading = False
        if ok:
            self.filepath = path
        self.set_modified(False)
        return ok

    def save(self) -> bool:
        if not self.filepath:
            return False
        if self.save_to_file(self.filepath):
            self.set_modified(False)
            return True
        return False

    def saveAs(self, path: str) -> bool:  # noqa: N802
        if self.save_to_file(path):
            self.filepath = path
            self.set_modified(False)
            return True
        return False

    # -- clone support ------------------------------------------------------------
    def clone_model(self) -> Todos:
        """Deep copy of the current board (dict round-trip)."""
        return todos_from_dict(todos_to_dict(self.todos()))

    # -- dirty tracking -----------------------------------------------------------
    def set_modified(self, modified: bool) -> None:
        if self._modified == modified:
            return
        self._modified = modified
        self.modificationChanged.emit(modified)

    def _on_changed(self) -> None:
        if not self._loading:
            self.set_modified(True)
