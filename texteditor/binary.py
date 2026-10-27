"""Classify files as text / image / video / audio / binary.

Used to decide which pane a file opens in: the text editor, the image
viewer, the video/audio players, or the binary placeholder stub.
"""
from __future__ import annotations

import os

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_BINARY = "binary"
KIND_PDF = "pdf"

_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp",
    ".tiff", ".tif", ".ico", ".heic", ".heif", ".raw",
}
_PDF_EXTS = {".pdf"}
_VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp",
}
_AUDIO_EXTS = {
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma",
    ".opus", ".mid", ".midi", ".aiff", ".aif",
}

# magic prefix -> kind
_MAGIC = [
    (b"\x89PNG", KIND_IMAGE),
    (b"\xff\xd8\xff", KIND_IMAGE),
    (b"GIF8", KIND_IMAGE),
    (b"BM", KIND_IMAGE),
    (b"RIFF", None),  # RIFF container: check for WEBP/WAV further in
    (b"%PDF", KIND_PDF),
    (b"ID3", KIND_AUDIO),
    (b"\xff\xfb", KIND_AUDIO),  # MPEG audio frame
    (b"\xff\xf3", KIND_AUDIO),
    (b"\xff\xf2", KIND_AUDIO),
    (b"OggS", KIND_AUDIO),
    (b"fLaC", KIND_AUDIO),
    (b"form", None),  # AIFF/WAV 'form' container
]


def _ext_kind(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        return KIND_IMAGE
    if ext in _VIDEO_EXTS:
        return KIND_VIDEO
    if ext in _AUDIO_EXTS:
        return KIND_AUDIO
    if ext in _PDF_EXTS:
        return KIND_PDF
    return None


def classify_file(path: str, fs=None) -> str:
    """Return the content kind for a file path."""
    ext_kind = _ext_kind(path)

    header = b""
    try:
        if fs is not None:
            data = fs.read_bytes(path)
            header = data[:64]
        else:
            with open(path, "rb") as fh:
                header = fh.read(64)
    except (OSError, FileNotFoundError):
        header = b""

    # Strong magic signatures override the extension.
    for magic, kind in _MAGIC:
        if header.startswith(magic):
            if kind is not None:
                return kind
            # RIFF: disambiguate WEBP (image) vs WAV (audio)
            if header[8:12] == b"WEBP":
                return KIND_IMAGE
            if header[8:12] == b"WAVE":
                return KIND_AUDIO
            return KIND_BINARY
    # 'form' (AIFF) container
    if header.startswith(b"form"):
        return KIND_AUDIO

    if ext_kind is not None:
        return ext_kind

    # No strong signal: try to see if it decodes as text.
    sample = header if header else b""
    if not sample:
        return KIND_TEXT
    if b"\x00" in sample:
        return KIND_BINARY
    try:
        sample.decode("utf-8")
        return KIND_TEXT
    except UnicodeDecodeError:
        return KIND_BINARY


def is_media(kind: str) -> bool:
    return kind in (KIND_IMAGE, KIND_VIDEO, KIND_AUDIO)


def is_image(path: str) -> bool:
    """Cheap extension-only image check (no file reads; safe for folder listing)."""
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS


def is_video(path: str) -> bool:
    """Cheap extension-only video check (no file reads; safe for folder listing)."""
    return os.path.splitext(path)[1].lower() in _VIDEO_EXTS


def is_audio(path: str) -> bool:
    """Cheap extension-only audio check (no file reads; safe for folder listing)."""
    return os.path.splitext(path)[1].lower() in _AUDIO_EXTS
