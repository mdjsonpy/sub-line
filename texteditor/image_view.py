"""Image preview pane: renders an image file with zoom, centered on a dark bg.

Supports static formats (png/jpg/bmp/webp/tif/ico/svg/...) and animated GIFs
(via QMovie). Formats Qt cannot decode (heic/heif/raw/...) fall back to a
"could not render" badge. Read-only — mirrors the EditorPane/BinaryPane
interface (title, isModified, save, saveAs) so tab management works unchanged.

Wheel: plain scroll pans vertically, Shift+scroll pans horizontally, and
Ctrl+scroll zooms in/out anchored at the mouse position.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtCore import QRectF, QEvent
from PySide6.QtGui import QPixmap, QMovie, QPainter, QColor, QFont, QPen, QBrush

from .binary import KIND_IMAGE, is_image
from .theme import EDITOR_BG

MAX_SIDE = 16384      # bound the source pixmap to avoid OOM on huge images
_ZOOM_STEP = 1.25
_ZOOM_MIN = 0.1
_ZOOM_MAX = 16.0
_FALLBACK_SIZE = QSize(260, 140)


class ImageCanvas(QWidget):
    """Paints a static pixmap or the current GIF frame, scaled by ``_scale``.

    Its ``sizeHint()`` reflects the scaled size so the enclosing QScrollArea
    shows scrollbars for large images and centers small ones.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._movie: QMovie | None = None
        self._fallback = False
        self._scale = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)

    # --- source loading --------------------------------------------------------
    def load(self, path: str, data: bytes | None = None) -> None:
        self.stop()
        self._pixmap = None
        self._movie = None
        self._fallback = False
        self._scale = 1.0

        if data is not None:
            pm = QPixmap()
            if pm.loadFromData(data):
                self._pixmap = self._bounded(pm)
            else:
                self._fallback = True
        else:
            pm = QPixmap(path)
            if not pm.isNull():
                self._pixmap = self._bounded(pm)
            else:
                movie = QMovie(path, None, self)
                if movie.isValid() and movie.frameCount() > 1:
                    self._movie = movie
                else:
                    self._fallback = True
        self.updateGeometry()
        self.update()

    @staticmethod
    def _bounded(pm: QPixmap) -> QPixmap:
        largest = max(pm.width(), pm.height())
        if largest <= MAX_SIDE:
            return pm
        factor = MAX_SIDE / largest
        return pm.scaled(
            max(1, int(pm.width() * factor)),
            max(1, int(pm.height() * factor)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def isFallback(self) -> bool:  # noqa: N802
        return self._fallback

    # --- sizing ----------------------------------------------------------------
    def _base_size(self) -> QSize:
        if self._movie is not None:
            im = self._movie.currentImage()
            if not im.isNull():
                return im.size()
            return _FALLBACK_SIZE
        if self._pixmap is not None and not self._pixmap.isNull():
            return self._pixmap.size()
        return _FALLBACK_SIZE

    def sizeHint(self) -> QSize:  # noqa: N802
        base = self._base_size()
        if base.isEmpty():
            return _FALLBACK_SIZE
        return QSize(
            max(1, int(base.width() * self._scale)),
            max(1, int(base.height() * self._scale)),
        )

    # --- zoom ------------------------------------------------------------------
    def scale(self) -> float:
        return self._scale

    def setScale(self, scale: float) -> None:  # noqa: N802
        self._scale = max(_ZOOM_MIN, min(_ZOOM_MAX, scale))
        self.updateGeometry()
        self.update()

    # --- painting --------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(EDITOR_BG))

        if self._fallback:
            self._paint_fallback(p)
            p.end()
            return

        if self._pixmap is not None and not self._pixmap.isNull():
            src = self._pixmap
        elif self._movie is not None:
            im = self._movie.currentImage()
            if im.isNull():
                p.end()
                return
            src = QPixmap.fromImage(im)
        else:
            p.end()
            return

        target = self.sizeHint()
        scaled = src.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()

    def _paint_fallback(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#4ec9b0")))
        p.drawRoundedRect(QRectF(8, 8, 96, 96), 18, 18)
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor("#1e1e1e"), 2))
        p.drawText(QRectF(0, 0, 112, 112), Qt.AlignCenter, "IMG")

        f2 = QFont()
        f2.setPointSize(10)
        p.setFont(f2)
        p.setPen(QColor("#8a8a8a"))
        p.drawText(QRectF(0, 112, 320, 30), Qt.AlignLeft | Qt.AlignTop,
                   "Could not render this image format.")

    # --- GIF animation (timer-driven; avoids the unbound frameChanged signal) ---
    def start(self) -> None:
        if self._movie is not None:
            self._movie.start()
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._movie is not None:
            self._movie.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().closeEvent(event)


class _ImageScrollArea(QScrollArea):
    """QScrollArea with wheel gestures and grab-pan:

    - plain wheel: vertical pan (default)
    - Shift + wheel: horizontal pan
    - Ctrl + wheel: zoom anchored at the mouse position
    - click + drag: move the viewport with the mouse
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag = None  # (start_global_pos, hbar_value, vbar_value)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        mods = event.modifiers()
        if Qt.ControlModifier in mods:
            self._zoom_at(event, delta > 0)
            return
        if Qt.ShiftModifier in mods:
            sb = self.horizontalScrollBar()
            if sb.maximum() > 0:
                sb.setValue(sb.value() - delta)
                return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            gpos = event.globalPosition().toPoint()
            self._drag = (
                gpos,
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and (event.buttons() & Qt.LeftButton):
            start, h0, v0 = self._drag
            d = event.globalPosition().toPoint() - start
            self.horizontalScrollBar().setValue(h0 - d.x())
            self.verticalScrollBar().setValue(v0 - d.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = None
        super().mouseReleaseEvent(event)

    def _zoom_at(self, event, zoom_in: bool) -> None:
        canvas = self.widget()
        if canvas is None or not hasattr(canvas, "setScale"):
            return
        old = canvas.scale()
        new = old * _ZOOM_STEP if zoom_in else old / _ZOOM_STEP
        gpos = event.globalPosition().toPoint()
        cpos = canvas.mapFromGlobal(gpos)
        ax = cpos.x() / old if old else 0.0
        ay = cpos.y() / old if old else 0.0
        canvas.setScale(new)
        canvas.resize(canvas.sizeHint())
        vp = self.viewport().mapFromGlobal(gpos)
        self.horizontalScrollBar().setValue(int(ax * new - vp.x()))
        self.verticalScrollBar().setValue(int(ay * new - vp.y()))


class ImagePane(QWidget):
    """Read-only image preview pane (one tab's content)."""

    modificationChanged = Signal(bool)
    pathChanged = Signal()          # emitted when filepath changes (navigation)
    navigateRequested = Signal(int) # Left = -1, Right = +1

    def __init__(self, path: str, parent=None, fs=None) -> None:
        super().__init__(parent)
        self.filepath: str | None = path
        self.kind = KIND_IMAGE
        self._fs = fs

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas = ImageCanvas(self)
        if fs is not None:
            try:
                data = fs.read_bytes(path)
                self.canvas.load(path, data=data)
            except (OSError, FileNotFoundError):
                self.canvas.load(path)
        else:
            self.canvas.load(path)

        self.scroll = _ImageScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll)

        # arrow-key navigation works regardless of which child holds focus
        self.canvas.installEventFilter(self)
        self.scroll.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)

    def _apply_scale(self, new_scale: float) -> None:
        self.canvas.setScale(new_scale)
        self.canvas.resize(self.canvas.sizeHint())

    # --- interface parity with EditorPane / BinaryPane -------------------------
    @property
    def title(self) -> str:
        return os.path.basename(self.filepath) if self.filepath else "Image"

    def isModified(self) -> bool:  # noqa: N802
        return False

    def save(self) -> bool:
        return False

    def saveAs(self, path: str) -> bool:  # noqa: N802
        return False

    def setKind(self, kind: str) -> None:
        pass  # ImagePane is always an image

    def isFallback(self) -> bool:  # noqa: N802
        return self.canvas.isFallback()

    # --- zoom ------------------------------------------------------------------
    def zoomIn(self) -> None:  # noqa: N802
        self._apply_scale(self.canvas.scale() * _ZOOM_STEP)

    def zoomOut(self) -> None:  # noqa: N802
        self._apply_scale(self.canvas.scale() / _ZOOM_STEP)

    def resetZoom(self) -> None:  # noqa: N802
        self._apply_scale(1.0)

    # --- navigation -------------------------------------------------------------
    def set_path(self, path: str) -> None:
        """Swap the displayed image file in place (title updates via pathChanged)."""
        path = os.path.abspath(path)
        if path == self.filepath:
            return
        self.filepath = path
        self.canvas.load(path)          # resets scale + repaints
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
            self.navigateRequested.emit(-1)
            return
        if event.key() == Qt.Key_Right:
            self.navigateRequested.emit(1)
            return
        super().keyPressEvent(event)


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
