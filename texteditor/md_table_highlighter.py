"""Syntax highlighting for Markdown tables (borders, header, separator).

Runs alongside :class:`texteditor.highlighter.PygmentsHighlighter`. Because
"header" / "in-table" are line-relational properties, the per-line flags are
precomputed on document revision change (cheap, O(lines)) and each
``highlightBlock`` call just applies them.
"""
from __future__ import annotations

from PySide6.QtGui import QSyntaxHighlighter

from .markdown_tables import line_flags, _pipe_indices
from .theme import Theme


class MarkdownTableHighlighter(QSyntaxHighlighter):
    def __init__(self, theme: Theme, document=None) -> None:
        # Set Python attributes before super().__init__(document): assigning a
        # non-empty document triggers an immediate rehighlight, which calls
        # highlightBlock() while __init__ is still running.
        self._theme = theme
        self._flags: tuple[list, list, list] = ([], [], [])
        self._cached_rev = -1
        super().__init__(document)
        if document is not None:
            document.contentsChanged.connect(self._on_contents_changed)

    def _on_contents_changed(self) -> None:
        self._cached_rev = -1

    def _rebuild(self, doc) -> None:
        lines = doc.toPlainText().split("\n")
        self._flags = line_flags(lines)
        self._cached_rev = doc.revision()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt naming)
        # Qt may rehighlight during construction (before Python attrs are bound);
        # skip then — editor_view rehighlights once the pane is fully set up.
        if not hasattr(self, "_cached_rev"):
            return
        doc = self.document()
        if doc is None:
            return
        if self._cached_rev != doc.revision():
            self._rebuild(doc)
        in_table, is_sep, is_header = self._flags
        block = self.currentBlock()
        number = block.blockNumber() if block.isValid() else -1
        if number < 0 or number >= len(in_table) or not in_table[number]:
            return

        if is_sep[number]:
            self.setFormat(0, len(text), self._theme.md_separator_format)
            return

        if is_header[number]:
            self.setFormat(0, len(text), self._theme.md_header_format)

        # Pipe borders always take the dedicated border color.
        for idx in _pipe_indices(text):
            self.setFormat(idx, 1, self._theme.md_pipe_format)
