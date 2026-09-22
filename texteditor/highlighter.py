"""Pygments-backed syntax highlighting for QPlainTextEdit documents.

Strategy: QSyntaxHighlighter calls ``highlightBlock`` per block, but Pygments
lexers are stateful across lines. To stay correct for multi-line constructs
(triple-quoted strings, block comments, ...) we re-tokenize the *whole*
document whenever its revision changes, build a per-block format map, and then
each ``highlightBlock`` call simply applies its block's precomputed formats.
"""
from __future__ import annotations

from PySide6.QtGui import QSyntaxHighlighter, QTextCursor
from PySide6.QtCore import QTimer
from pygments.lexers import get_lexer_for_filename, get_lexer_by_name
from pygments.util import ClassNotFound

from .theme import Theme


def lexer_for_path(path: str, sample: str = ""):
    """Return the best Pygments lexer for a file path, falling back to plain text."""
    try:
        return get_lexer_for_filename(path, sample or None)
    except ClassNotFound:
        pass
    try:
        return get_lexer_by_name("text")
    except Exception:  # pragma: no cover - extremely unlikely
        from pygments.lexers import TextLexer

        return TextLexer()


class PygmentsHighlighter(QSyntaxHighlighter):
    """Applies Pygments token colors to a QPlainTextEdit document."""

    def __init__(self, lexer, theme: Theme, document=None) -> None:
        super().__init__(document)
        self._lexer = lexer
        self._theme = theme
        self._cached_rev = -1
        self._block_map: dict[int, list] = {}
        self._suppress = False
        # Debounced rehighlight so typing doesn't tokenize on every keystroke.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._debounced_rehighlight)
        if document is not None:
            document.contentsChanged.connect(self._on_contents_changed)

    def _on_contents_changed(self) -> None:
        if self._suppress:
            return
        self._timer.start()

    def _safe_rehighlight(self) -> None:
        self._suppress = True
        try:
            self.rehighlight()
        finally:
            self._suppress = False

    def _debounced_rehighlight(self) -> None:
        self._safe_rehighlight()

    def set_lexer(self, lexer) -> None:
        if lexer is self._lexer:
            return
        self._lexer = lexer
        self._cached_rev = -1
        self._safe_rehighlight()

    def _rebuild(self, doc) -> None:
        full = doc.toPlainText()
        try:
            tokens = list(self._lexer.get_tokens(full))
        except Exception:
            tokens = []

        block_map: dict[int, list] = {}
        pos = 0
        for tok_type, value in tokens:
            start = pos
            length = len(value)
            end = start + length
            pos = end
            if length == 0:
                continue
            fmt = self._theme.format_for_token(tok_type)
            if fmt is None:
                continue
            c = QTextCursor(doc)
            c.setPosition(start)
            block = c.block()
            while block.isValid() and block.position() < end:
                bs = block.position()
                be = bs + len(block.text())
                s = max(start, bs)
                e = min(end, be)
                if e > s:
                    block_map.setdefault(block.blockNumber(), []).append((s - bs, e - s, fmt))
                block = block.next()
        self._block_map = block_map
        self._cached_rev = doc.revision()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt naming)
        doc = self.document()
        if doc is None:
            return
        if self._cached_rev != doc.revision():
            self._rebuild(doc)
        block = self.currentBlock()
        number = block.blockNumber() if block.isValid() else -1
        for pos, length, fmt in self._block_map.get(number, ()):
            self.setFormat(pos, length, fmt)
