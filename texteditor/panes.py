"""Single dispatch point: file path -> the correct pane instance."""
from __future__ import annotations

from .binary import classify_file, KIND_IMAGE, KIND_TEXT
from .editor_view import EditorPane
from .image_view import ImagePane
from .binary_stub import BinaryPane
from .todos_pane import TodosPane
from .theme import Theme


def make_pane(path: str, theme: Theme, fs=None):
    if path.lower().endswith(".todo"):
        pane = TodosPane(fs=fs)
        if pane.load(path):
            return pane
        pane.deleteLater()
    kind = classify_file(path, fs=fs)
    if kind == KIND_TEXT:
        pane = EditorPane(theme, fs=fs)
        pane.loadFile(path)
    elif kind == KIND_IMAGE:
        pane = ImagePane(path, fs=fs)
    else:
        pane = BinaryPane(path, kind, fs=fs)
    return pane
