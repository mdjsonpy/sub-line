"""Tests for the Markdown table helpers, highlighter theme, and editor behavior.

Run with:  ./.venv/bin/python -m pytest tests/test_markdown_tables.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texteditor import markdown_tables as mt


# ---------------------------------------------------------------------------
# markdown_tables — pure functions
# ---------------------------------------------------------------------------
def test_is_markdown_path() -> None:
    assert mt.is_markdown_path("a/b/file.md")
    assert mt.is_markdown_path("notes.MARKDOWN")
    assert not mt.is_markdown_path("a/b/file.txt")
    assert not mt.is_markdown_path("")


def test_split_cells() -> None:
    assert mt.split_cells("| a | bb | ccc |") == ["a", "bb", "ccc"]
    assert mt.split_cells("|a|b|") == ["a", "b"]
    # single pipe is not a table row
    assert mt.split_cells("no table here |") == []
    # escaped pipe is not a separator
    assert mt.split_cells(r"| a \| b | c |") == ["a \\| b", "c"]


def test_is_table_row() -> None:
    assert mt.is_table_row("| a | b |")
    assert mt.is_table_row("|a|b|")
    assert not mt.is_table_row("plain text")
    assert not mt.is_table_row("a | b")


def test_is_separator_row() -> None:
    assert mt.is_separator_row("|---|---|")
    assert mt.is_separator_row("| --- | :---: | ---: |")
    assert mt.is_separator_row("| - | - |")
    assert not mt.is_separator_row("| a | b |")
    assert not mt.is_separator_row("| -- x |")


def test_cell_spans() -> None:
    spans = mt.cell_spans("| a | bb |")
    assert spans == [(2, 3), (6, 8)]
    line = "| a | bb |"
    for s, e in spans:
        assert line[s:e] in ("a", "bb")


def test_find_table_blocks() -> None:
    lines = [
        "intro",
        "| a | b |",
        "| - | - |",
        "| c | d |",
        " outro ",
        "```",
        "| a | b |",
        "| c | d |",
        "```",
    ]
    blocks = mt.find_table_blocks(lines)
    assert blocks == [(1, 3)]  # the fenced one is excluded


def test_align_block_aligns_columns() -> None:
    lines = ["| a | bb |", "| --- | --- |", "| ccc | d |"]
    out = mt.align_block(lines, 0, 2)
    assert out == [
        "| a   | bb  |",
        "| --- | --- |",
        "| ccc | d   |",
    ]


def test_align_block_preserves_alignment_markers() -> None:
    lines = ["| a | bb | ccc |", "| --- | :---: | ---: |"]
    out = mt.align_block(lines, 0, 1)
    assert out[1] == "| --- | :---: | ---: |"


def test_align_all() -> None:
    lines = ["x", "| a | bb |", "| - | - |", "| c | d |", "y"]
    out, changed = mt.align_all(lines)
    assert changed
    assert out[0] == "x" and out[-1] == "y"
    assert out[1] == "| a   | bb  |"
    assert out[3] == "| c   | d   |"


def test_line_flags() -> None:
    lines = ["| a | b |", "| --- | --- |", "| c | d |"]
    in_table, is_sep, is_header = mt.line_flags(lines)
    assert in_table == [True, True, True]
    assert is_sep == [False, True, False]
    assert is_header == [True, False, False]


def test_align_is_idempotent() -> None:
    lines = ["| a | bb |", "| - | - |", "| c | dd |"]
    once = mt.align_block(lines, 0, 2)
    twice = mt.align_block(once, 0, 2)
    assert once == twice


# ---------------------------------------------------------------------------
# theme — table color attributes
# ---------------------------------------------------------------------------
def test_theme_has_table_formats() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from texteditor.theme import Theme

    t = Theme()
    assert t.md_pipe_format is not None
    assert t.md_separator_format is not None
    assert t.md_header_format is not None
    # header is bold
    from PySide6.QtGui import QFont

    assert t.md_header_format.fontWeight() == QFont.Bold


# ---------------------------------------------------------------------------
# editor_view — Tab / Enter navigation + formatting
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app() -> "QApplication":  # noqa: F821
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if existing is None:
        existing = QApplication([])
    return existing


def _pane(app, theme, path: str):
    import texteditor.editor_view as ev

    return ev.EditorPane(theme, path)


def _set_pos(pane, pos: int) -> None:
    from PySide6.QtGui import QTextCursor

    c = QTextCursor(pane.document())
    c.setPosition(pos)
    pane.setTextCursor(c)


@pytest.mark.usefixtures("app")
class TestEditorTableBehavior:
    def test_tab_moves_to_next_cell(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | - |\n")
        pane = self._pane(p)
        doc = pane.document()
        row0 = doc.findBlockByNumber(0)
        self._set_pos(pane, row0.position() + 2)  # inside cell 0 ("a")
        assert pane._handle_table_tab(False) is True
        # now at cell 1
        col = pane.textCursor().position() - row0.position()
        spans = mt.cell_spans(row0.text())
        assert spans[1][0] <= col <= spans[1][1]

    def test_tab_shift_moves_to_previous_cell(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | - |\n")
        pane = self._pane(p)
        doc = pane.document()
        row0 = doc.findBlockByNumber(0)
        self._set_pos(pane, row0.position() + 5)  # inside cell 1 ("bb")
        assert pane._handle_table_tab(True) is True
        col = pane.textCursor().position() - row0.position()
        spans = mt.cell_spans(row0.text())
        assert spans[0][0] <= col <= spans[0][1]

    def test_tab_at_last_cell_goes_to_next_row(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb | ccc |\n| - | - | - |\n| x | y | z |\n")
        pane = self._pane(p)
        doc = pane.document()
        row0 = doc.findBlockByNumber(0)
        row1 = doc.findBlockByNumber(1)
        # last cell of row0
        spans = mt.cell_spans(row0.text())
        self._set_pos(pane, row0.position() + spans[-1][0])
        assert pane._handle_table_tab(False) is True
        # should now be in row1's first cell
        assert pane.textCursor().blockNumber() == 1
        col = pane.textCursor().position() - row1.position()
        r1spans = mt.cell_spans(row1.text())
        assert r1spans[0][0] <= col <= r1spans[0][1]

    def test_tab_not_in_table_does_nothing(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("just plain text\n")
        pane = self._pane(p)
        self._set_pos(pane, 0)
        assert pane._handle_table_tab(False) is False

    def test_enter_inserts_row(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | - |\n")
        pane = self._pane(p)
        doc = pane.document()
        row0 = doc.findBlockByNumber(0)
        self._set_pos(pane, row0.position() + 2)  # inside cell 0
        assert pane._handle_table_enter() is True
        # a new row was inserted right after row0 -> cursor in block 1
        assert pane.textCursor().blockNumber() == 1
        text = doc.toPlainText()
        assert text.count("|") > 0  # table still intact

    def test_enter_in_last_cell_of_last_row_exits(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | --- |\n")
        pane = self._pane(p)
        doc = pane.document()
        last = doc.findBlockByNumber(1)
        spans = mt.cell_spans(last.text())
        self._set_pos(pane, last.position() + spans[-1][0])
        # last cell of last row -> Enter should NOT insert a row
        assert pane._handle_table_enter() is False

    def test_format_table(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | - |\n| c | dd |\n")
        pane = self._pane(p)
        doc = pane.document()
        self._set_pos(pane, doc.findBlockByNumber(0).position() + 2)
        assert pane.format_table() is True
        out = doc.toPlainText().split("\n")
        assert out[0] == "| a   | bb  |"
        assert out[2] == "| c   | dd  |"

    def test_format_all_tables(self, tmp_path) -> None:
        from texteditor.theme import Theme

        p = tmp_path / "t.md"
        p.write_text("| a | bb |\n| - | - |\n\n|x | yyy|\n|-|-|\n")
        pane = self._pane(p)
        assert pane.format_all_tables() is True
        out = pane.document().toPlainText().split("\n")
        assert out[0] == "| a   | bb  |"
        assert out[1] == "| --- | --- |"
        assert out[3] == "| x   | yyy |"
        assert out[4] == "| --- | --- |"

    # helpers
    def _pane(self, path) -> "object":
        from texteditor.theme import Theme

        import texteditor.editor_view as ev

        pane = ev.EditorPane(Theme())
        pane.loadFile(str(path))
        return pane

    def _set_pos(self, pane, pos: int) -> None:
        from PySide6.QtGui import QTextCursor

        c = QTextCursor(pane.document())
        c.setPosition(pos)
        pane.setTextCursor(c)
