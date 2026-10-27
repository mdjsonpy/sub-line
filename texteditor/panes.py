"""Single dispatch point: file path -> the correct pane instance."""
from __future__ import annotations

from .binary import classify_file, KIND_AUDIO, KIND_IMAGE, KIND_TEXT, KIND_VIDEO
from .editor_view import EditorPane
from .image_view import ImagePane
from .binary_stub import BinaryPane
from .todos_pane import TodosPane
from .theme import Theme

try:
    from .video_view import VideoPane
    from .audio_view import AudioPane
    _HAS_MEDIA = True
except ImportError:  # QtMultimedia not available on this machine
    VideoPane = AudioPane = None
    _HAS_MEDIA = False


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
    elif kind == KIND_VIDEO and _HAS_MEDIA:
        pane = VideoPane(path, fs=fs)
    elif kind == KIND_AUDIO and _HAS_MEDIA:
        pane = AudioPane(path, fs=fs)
    else:
        pane = BinaryPane(path, kind, fs=fs)
    return pane
