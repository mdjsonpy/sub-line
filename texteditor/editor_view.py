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
from . import markdown_tables as md_tables
from .md_table_highlighter import MarkdownTableHighlighter
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
        self._md_table_highlighter: MarkdownTableHighlighter | None = None
        self.is_markdown = False
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
        mods = event.modifiers()
        plain = not mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            if plain:
                back = k == Qt.Key_Backtab or bool(mods & Qt.ShiftModifier)
                if self.is_markdown and self._handle_table_tab(back):
                    return
                self._handle_tab(back)
                return
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            if plain and self.is_markdown and self._handle_table_enter():
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

    # --- markdown table editing -------------------------------------------------
    @staticmethod
    def _current_cell(spans: list[tuple[int, int]], col: int) -> int | None:
        for i, (s, e) in enumerate(spans):
            if s <= col <= e:
                return i
        return None

    def _set_cursor_to_cell(self, block, content_start: int) -> None:
        c = self.textCursor()
        c.setPosition(block.position() + content_start)
        self.setTextCursor(c)

    def _handle_table_tab(self, back: bool) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        spans = md_tables.cell_spans(block.text())
        if not spans:
            return False
        col = cursor.position() - block.position()
        idx = self._current_cell(spans, col)
        if idx is None:
            if not back:
                right = next((i for i, (s, _e) in enumerate(spans) if s > col), None)
                idx = (right - 1) if right is not None else (len(spans) - 1)
            else:
                left = next((i for i, (_s, e) in enumerate(spans) if e < col), None)
                idx = (left + 1) if left is not None else 0
        if not back:
            target = idx + 1
            if target >= len(spans):
                nb = block.next()
                nspans = md_tables.cell_spans(nb.text()) if nb.isValid() else []
                if nspans:
                    self._set_cursor_to_cell(nb, nspans[0][0])
                    return True
                target = 0
            self._set_cursor_to_cell(block, spans[target][0])
            return True
        target = idx - 1
        if target < 0:
            pb = block.previous()
            pspans = md_tables.cell_spans(pb.text()) if pb.isValid() else []
            if pspans:
                self._set_cursor_to_cell(pb, pspans[-1][0])
                return True
            target = len(spans) - 1
        self._set_cursor_to_cell(block, spans[target][0])
        return True

    def _handle_table_enter(self) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        line = block.text()
        spans = md_tables.cell_spans(line)
        if not spans:
            return False
        doc = self.document()
        lines = doc.toPlainText().split("\n")
        bn = block.blockNumber()
        block_range = next(
            ((s, e) for s, e in md_tables.find_table_blocks(lines) if s <= bn <= e),
            None,
        )
        if block_range is None:
            return False
        s, e = block_range
        idx = self._current_cell(spans, cursor.position() - block.position())
        idx = len(spans) - 1 if idx is None else idx
        if bn == e and idx == len(spans) - 1:
            return False  # last cell of last row -> exit the table
        ncols = len(spans)
        new_row = "| " + " | ".join([""] * ncols) + " |"
        end_pos = block.position() + len(line)
        c = QTextCursor(doc)
        c.setPosition(end_pos)
        c.insertText("\n" + new_row)
        target_cell = min(idx, ncols - 1)
        new_block_index = bn + 1
        self._align_block_range(s, e + 1)
        nb = doc.findBlockByNumber(new_block_index)
        nspans = md_tables.cell_spans(nb.text()) if nb.isValid() else []
        if nspans and target_cell < len(nspans):
            self._set_cursor_to_cell(nb, nspans[target_cell][0])
        return True

    def _align_block_range(self, s: int, e: int) -> bool:
        doc = self.document()
        lines = doc.toPlainText().split("\n")
        if s < 0 or s > e or e >= len(lines):
            return False
        new_rows = md_tables.align_block(lines, s, e)
        if new_rows == lines[s:e + 1]:
            return False
        start_block = doc.findBlockByNumber(s)
        end_block = doc.findBlockByNumber(e)
        c = QTextCursor(doc)
        c.setPosition(start_block.position())
        c.setPosition(end_block.position() + len(end_block.text()), QTextCursor.KeepAnchor)
        c.insertText("\n".join(new_rows))
        return True

    def format_table(self) -> bool:
        """Align the table block containing the cursor. Returns True if changed."""
        if not self.is_markdown:
            return False
        doc = self.document()
        lines = doc.toPlainText().split("\n")
        bn = self.textCursor().blockNumber()
        target = next(
            ((s, e) for s, e in md_tables.find_table_blocks(lines) if s <= bn <= e), None
        )
        if target is None:
            return False
        return self._align_block_range(*target)

    def format_all_tables(self) -> bool:
        """Align every table in the document. Returns True if anything changed."""
        if not self.is_markdown:
            return False
        doc = self.document()
        lines = doc.toPlainText().split("\n")
        new_lines, changed = md_tables.align_all(lines)
        if not changed:
            return False
        pos = self.textCursor().position()
        c = QTextCursor(doc)
        c.select(QTextCursor.Document)
        c.insertText("\n".join(new_lines))
        try:
            c = QTextCursor(doc)
            c.setPosition(min(pos, doc.characterCount() - 1))
            self.setTextCursor(c)
        except RuntimeError:
            self.moveCursor(QTextCursor.Start)
        return True

    # --- internal --------------------------------------------------------------
    def _on_modification_changed(self, modified: bool) -> None:
        if self._loading:
            return
        self.modificationChanged.emit(modified)

    def _setup_highlighter(self, path: str, text: str) -> None:
        lexer = lexer_for_path(path, text[:4096])
        self._highlighter = PygmentsHighlighter(lexer, self._theme, self.document())
        self._highlighter.rehighlight()

        self.is_markdown = md_tables.is_markdown_path(path) or (
            (getattr(lexer, "name", "") or "").lower() == "markdown"
        )
        if self.is_markdown:
            if self._md_table_highlighter is None:
                self._md_table_highlighter = MarkdownTableHighlighter(
                    self._theme, self.document()
                )
            self._md_table_highlighter.rehighlight()
        else:
            self._md_table_highlighter = None

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
