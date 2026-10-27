"""Guard against opening very large text files in the editor.

The editor loads a file fully into memory (bytes + chardet scan + document),
so a multi-gigabyte text file can freeze or crash the app. Media files are
streamed and images are size-bounded, so only text files are gated here.

Policy:
  * <= WARN_SIZE: open without asking.
  *  WARN_SIZE < size <= MAX_SIZE: ask the user first.
  *  size > MAX_SIZE: refuse to open.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QMessageBox

WARN_SIZE = 10 * 1024 * 1024    # 10 MiB: ask before opening
MAX_SIZE = 256 * 1024 * 1024    # 256 MiB: hard limit for the editor


def file_size(path: str, fs=None) -> int | None:
    """Size in bytes, or None if it cannot be determined."""
    if fs is not None:
        try:
            return fs.size(path)
        except (OSError, FileNotFoundError, AttributeError):
            return None
    try:
        return os.stat(path).st_size
    except (OSError, FileNotFoundError):
        return None


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num} TB"


def confirm_open(path: str, fs=None, parent=None) -> bool:
    """Return True if ``path`` may be opened in the editor.

    Prompts for files above WARN_SIZE and refuses files above MAX_SIZE.
    """
    size = file_size(path, fs)
    if size is None or size <= WARN_SIZE:
        return True

    name = os.path.basename(path) or path

    if size > MAX_SIZE:
        QMessageBox.warning(
            parent,
            "File too large",
            f"'{name}' is {human_size(size)}.\n\n"
            f"Files larger than {human_size(MAX_SIZE)} are too big to open "
            f"in the editor.",
        )
        return False

    ret = QMessageBox.question(
        parent,
        "Large file",
        f"'{name}' is {human_size(size)}.\n\n"
        f"Opening large files can be slow. Open it anyway?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return ret == QMessageBox.Yes
