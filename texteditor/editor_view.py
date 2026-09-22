"""The text editor pane: QPlainTextEdit with line numbers, syntax highlighting,
zoom, word wrap, whitespace visibility, and save/load with encoding detection.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QWidget, QPlainTextEdit, QTextEdit
from PySide6.QtCore import Qt, Signal, QSize, QRect
from PySide6.QtGui import (
    QTextCharFormat,
    QTextCursor,
    QColor,
    QPainter,
)
import chardet

from .highlighter import PygmentsHighlighter, lexer_for_path
from .theme import Theme, EDITOR_BG

TAB_SIZE = 4
_WS_COLOR = QColor("#6e6e6e")


class LineNumberArea(QWidget):
    def __init__(self, editor: "EditorPane") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._editor._paint_line_number_area(event)


class EditorPane(QPlainTextEdit):
    """A single text document pane (one tab's content)."""

    modificationChanged = Signal(bool)

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.filepath: str | None = None
        self.encoding: str | None = "utf-8"
        self._highlighter: PygmentsHighlighter | None = None
        self._show_whitespace = False
        self._zoom = 0  # offset from base size
        self._base_size = theme.editor_font.pointSize()
        self._loading = False

        # Editor setup
        self.setFont(theme.editor_font)
        self.setTabStopDistance(TAB_SIZE * self.fontMetrics().horizontalAdvance(" "))
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setCenterOnScroll(False)

        # Line number area
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._update_line_numbers)

        doc = self.document()
        doc.modificationChanged.connect(self._on_modification_changed)

        self._update_line_number_area_width(0)

    # --- public API ------------------------------------------------------------
    @property
    def title(self) -> str:
        if self.filepath:
            return os.path.basename(self.filepath)
        return "Untitled"

    def isModified(self) -> bool:  # noqa: N802
        return self.document().isModified()

    def setKind(self, kind: str) -> None:
        pass  # EditorPane is always text

    def loadFile(self, path: str) -> None:
        with open(path, "rb") as fh:
            raw = fh.read()
        self.encoding = _detect_encoding(raw)
        try:
            text = raw.decode(self.encoding)
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("utf-8", "replace")
            self.encoding = "utf-8"

        self._loading = True
        self.setPlainText(text)
        self.document().setModified(False)
        self._loading = False

        self._setup_highlighter(path, text)
        self.filepath = path
        self._set_tab_stop()
        self._refresh_whitespace()
        # reset cursor
        self.moveCursor(QTextCursor.Start)

    def save(self) -> bool:
        if not self.filepath:
            return False
        return self._write(self.filepath)

    def saveAs(self, path: str) -> bool:  # noqa: N802
        ok = self._write(path)
        if ok:
            self.filepath = path
            self._setup_highlighter(path, self.toPlainText())
        return ok

    def _write(self, path: str) -> bool:
        try:
            data = self.toPlainText().encode(self.encoding or "utf-8")
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            return False
        self.document().setModified(False)
        self.filepath = path
        return True

    # --- zoom ------------------------------------------------------------------
    def zoomIn(self) -> None:  # noqa: N802
        self._set_zoom(self._zoom + 1)

    def zoomOut(self) -> None:  # noqa: N802
        self._set_zoom(self._zoom - 1)

    def resetZoom(self) -> None:  # noqa: N802
        self._set_zoom(0)

    def set_zoom(self, offset: int) -> None:
        self._set_zoom(offset)

    def zoom_offset(self) -> int:
        return self._zoom

    def _set_zoom(self, zoom: int) -> None:
        zoom = max(-8, min(16, zoom))
        self._zoom = zoom
        size = max(6, self._base_size + zoom)
        f = self._theme.editor_font
        f.setPointSize(size)
        self.setFont(f)
        self._set_tab_stop()

    def _set_tab_stop(self) -> None:
        self.setTabStopDistance(TAB_SIZE * self.fontMetrics().horizontalAdvance(" "))

    # --- wrap ------------------------------------------------------------------
    def setWrap(self, on: bool) -> None:
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    def wrapEnabled(self) -> bool:
        return self.lineWrapMode() == QPlainTextEdit.WidgetWidth

    # --- whitespace ------------------------------------------------------------
    def setShowWhitespace(self, on: bool) -> None:
        self._show_whitespace = on
        self._refresh_whitespace()

    def showWhitespace(self) -> bool:
        return self._show_whitespace

    def _refresh_whitespace(self) -> None:
        if not self._show_whitespace:
            self.setExtraSelections([])
            return
        selections = []
        doc = self.document()
        block = doc.begin()
        space_fmt = QTextCharFormat()
        space_fmt.setForeground(_WS_COLOR)
        tab_fmt = QTextCharFormat()
        tab_fmt.setForeground(_WS_COLOR)
        tab_fmt.setFontUnderline(True)
        tab_fmt.setUnderlineColor(_WS_COLOR)
        while block.isValid():
            text = block.text()
            # trailing whitespace
            end = len(text)
            start = end
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            if start < end:
                for i in range(start, end):
                    ch = text[i]
                    sel = self._make_selection(block, i, space_fmt if ch == " " else tab_fmt)
                    selections.append(sel)
            block = block.next()
        self.setExtraSelections(selections)

    def _make_selection(self, block, index: int, fmt: QTextCharFormat):
        sel = QTextEdit.ExtraSelection()
        sel.cursor = QTextCursor(block)
        sel.cursor.setPosition(block.position() + index)
        sel.cursor.setPosition(block.position() + index + 1, QTextCursor.KeepAnchor)
        sel.format = fmt
        return sel

    # --- tab / indent ----------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        k = event.key()
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            # plain Tab / Shift+Tab only; let Ctrl+Tab (tab switching) pass through
            if not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
                self._handle_tab(k == Qt.Key_Backtab or bool(event.modifiers() & Qt.ShiftModifier))
                return
        super().keyPressEvent(event)

    def _handle_tab(self, back: bool) -> None:
        if back:
            self._dedent_line()
            return
        cursor = self.textCursor()
        doc = self.document()
        if cursor.hasSelection() and self._block_number(doc, cursor.selectionStart()) != self._block_number(doc, cursor.selectionEnd()):
            self._indent_selection()
        else:
            self.insertPlainText(TAB_SIZE * " ")

    def _indent_selection(self) -> None:
        cursor = self.textCursor()
        doc = self.document()
        start_block = self._block_number(doc, cursor.selectionStart())
        end_block = self._block_number(doc, cursor.selectionEnd())
        cursor.clearSelection()
        for bn in range(end_block, start_block - 1, -1):
            block = self.document().findBlockByNumber(bn)
            if block.isValid():
                c = QTextCursor(block)
                c.insertText(TAB_SIZE * " ")
        self.setTextCursor(cursor)

    @staticmethod
    def _block_number(doc, position: int) -> int:
        c = QTextCursor(doc)
        c.setPosition(position)
        return c.blockNumber()

    def _dedent_line(self) -> None:
        cursor = self.textCursor()
        block = self.document().findBlockByNumber(cursor.blockNumber())
        text = block.text()
        n = 0
        while n < len(text) and text[n] == " " and n < TAB_SIZE:
            n += 1
        if n:
            c = QTextCursor(block)
            c.setPosition(block.position())
            c.setPosition(block.position() + n, QTextCursor.KeepAnchor)
            c.removeSelectedText()
            self.setTextCursor(c)

    # --- internal --------------------------------------------------------------
    def _on_modification_changed(self, modified: bool) -> None:
        if self._loading:
            return
        self.modificationChanged.emit(modified)

    def _setup_highlighter(self, path: str, text: str) -> None:
        lexer = lexer_for_path(path, text[:4096])
        self._highlighter = PygmentsHighlighter(lexer, self._theme, self.document())
        self._highlighter.rehighlight()

    # --- line number area ------------------------------------------------------
    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        space = 13 + self.fontMetrics().horizontalAdvance("9") * digits
        return space

    def _update_line_number_area_width(self, _new_blocks: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_numbers(self) -> None:
        self.line_number_area.update()

    def _update_line_number_area(self, rect: "object", dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(
                0, rect.y(), self.line_number_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(self.blockCount())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        r = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(r.left(), r.top(), self.line_number_area_width(), r.height())
        )

    def _paint_line_number_area(self, event) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(EDITOR_BG))

        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        painter.setPen(QColor("#858585"))
        font = self.font()
        painter.setFont(font)
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = block.blockNumber() + 1
                painter.drawText(
                    0, top, self.line_number_area_width() - 5,
                    self.fontMetrics().height(),
                    Qt.AlignRight, str(number),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
        painter.end()


def _detect_encoding(raw: bytes) -> str:
    if not raw:
        return "utf-8"
    det = chardet.detect(raw)
    enc = (det or {}).get("encoding")
    if enc:
        return enc.lower().replace("-", "")
    return "utf-8"
