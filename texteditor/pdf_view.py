"""Read-only PDF viewer pane backed by QtPdf (``QPdfView`` + ``QPdfDocument``).

PDFs are rendered natively by Qt, so no external process is required — unlike
audio/video, which need an OS player. Encrypted (memory) workspaces have no real
path on disk, so the bytes are spilled to a short-lived temp file for the
document to load from; it is removed when the pane is destroyed.
"""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

try:  # QtPdf may be absent in minimal installs; degrade to an informative stub.
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView

    _HAS_QTPDF = True
except Exception:  # pragma: no cover - only when QtPdf is not installed
    QPdfDocument = QPdfView = None
    _HAS_QTPDF = False


class _NoPdf(QWidget):
    """Placeholder shown when QtPdf is unavailable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        label = QLabel("PDF preview needs QtPdf, which is not installed.", self)
        label.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.addWidget(label)

    @property
    def title(self) -> str:
        return "PDF"

    def isModified(self) -> bool:
        return False

    def save(self) -> bool:
        return False

    def saveAs(self, path: str) -> bool:
        return False


class PdfPane(QWidget):
    """A non-editable pane that renders a PDF read from disk or a WorkspaceFS."""

    modificationChanged = Signal(bool)

    def __init__(self, path: str, parent: QWidget | None = None, fs=None) -> None:
        super().__init__(parent)
        self.filepath = path
        self._fs = fs
        self._tmp: str | None = None
        self._view: QWidget | None = None
        self._doc = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        if not _HAS_QTPDF:
            self._view = _NoPdf(self)
            lay.addWidget(self._view)
            return

        doc = QPdfDocument(self)
        self._doc = doc
        try:
            if fs is not None:
                data = fs.read_bytes(path)
                fd, name = tempfile.mkstemp(suffix=".pdf", prefix="subline_pdf_")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                self._tmp = name
                doc.load(name)
            else:
                doc.load(path)
        except Exception:
            # Leave the document empty; the view shows a blank page rather than crash.
            pass

        self._view = QPdfView(self)
        self._view.setDocument(doc)
        lay.addWidget(self._view)

    # --- Pane protocol -----------------------------------------------------
    @property
    def title(self) -> str:
        return os.path.basename(self.filepath) if self.filepath else "PDF"

    def isModified(self) -> bool:
        return False

    def save(self) -> bool:
        return False

    def saveAs(self, path: str) -> bool:
        return False

    # --- lifecycle ---------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._cleanup_tmp()
        super().closeEvent(event)

    def _cleanup_tmp(self) -> None:
        if self._tmp:
            try:
                os.unlink(self._tmp)
            except OSError:
                pass
            self._tmp = None
