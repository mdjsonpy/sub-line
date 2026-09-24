"""Dark Sublime-like theme: global QSS stylesheet + Pygments token palette."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from pygments.token import (
    Token,
    Comment,
    Keyword,
    Name,
    Literal,
    String,
    Number,
    Operator,
    Punctuation,
    Generic,
    Error,
)

EDITOR_BG = "#1e1e1e"
EDITOR_FG = "#d4d4d4"
ACCENT = "#094771"
MONO_FONT_FAMILY = "DejaVu Sans Mono"
MONO_FONT_SIZE = 13
MONO_FONT_WEIGHT = 400

# Markdown table display colors.
MD_TABLE_PIPE = "#8a8a8a"        # column borders (vertical bars)
MD_TABLE_SEPARATOR = "#6a9955"   # the | --- | --- | alignment row
MD_TABLE_HEADER = "#569cd6"      # first row (right above the separator)

# Map Pygments token types -> hex color. Lookup walks .parent for fallbacks.
PYGMENTS_COLORS: dict = {
    Token: "#d4d4d4",
    Token.Comment: "#6a9955",
    Token.Comment.Preproc: "#d7ba7d",
    Token.Comment.Special: "#d7ba7d",
    Token.Keyword: "#c586c0",
    Token.Keyword.Namespace: "#569cd6",
    Token.Keyword.Type: "#4ec9b0",
    Token.Keyword.Pseudo: "#569cd6",
    Token.Name: "#9cdcfe",
    Token.Name.Function: "#dcdcaa",
    Token.Name.Function.Magic: "#dcdcaa",
    Token.Name.Class: "#4ec9b0",
    Token.Name.Builtin: "#4ec9b0",
    Token.Name.Builtin.Pseudo: "#4ec9b0",
    Token.Name.Decorator: "#dcdcaa",
    Token.Name.Variable: "#9cdcfe",
    Token.Name.Variable.Global: "#9cdcfe",
    Token.Name.Constant: "#4fc1ff",
    Token.Name.Label: "#9cdcfe",
    Token.Name.Attribute: "#9cdcfe",
    Token.Literal: "#569cd6",
    Token.Literal.String: "#ce9178",
    Token.Literal.String.Doc: "#6a9955",
    Token.Literal.String.Interpol: "#ce9178",
    Token.Literal.String.Escape: "#d4d4d4",
    Token.String: "#ce9178",
    Token.String.Doc: "#6a9955",
    Token.String.Interpol: "#ce9178",
    Token.String.Escape: "#d4d4d4",
    Token.Number: "#b5cea8",
    Token.Number.Integer: "#b5cea8",
    Token.Number.Float: "#b5cea8",
    Token.Operator: "#d4d4d4",
    Token.Operator.Word: "#c586c0",
    Token.Punctuation: "#d4d4d4",
    Token.Punctuation.Def: "#d4d4d4",
    Token.Generic: "#d4d4d4",
    Token.Generic.Deleted: "#cd3131",
    Token.Generic.Inserted: "#228724",
    Token.Generic.Error: "#cc0000",
    Token.Generic.Heading: "#569cd6",
    Token.Generic.Subheading: "#569cd6",
    Token.Generic.Strong: "#d4d4d4",
    Token.Generic.Emph: "#d4d4d4",
    Token.Error: "#f44747",
    Token.Background: "#1e1e1e",
    Token.Other: "#d4d4d4",
}


def _hex_to_color(hexstr: str) -> QColor:
    return QColor(hexstr)


class Theme:
    """Builds QSS stylesheet and per-token char formats."""

    def __init__(self) -> None:
        self._editor_font = QFont(MONO_FONT_FAMILY, MONO_FONT_SIZE, MONO_FONT_WEIGHT)
        self._token_formats: dict = {}
        self._md_pipe_format = None
        self._md_separator_format = None
        self._md_header_format = None
        self._build_token_formats()

    @property
    def editor_font(self) -> QFont:
        return QFont(self._editor_font)

    def _build_token_formats(self) -> None:
        from PySide6.QtGui import QTextCharFormat

        for tok_type, hexstr in PYGMENTS_COLORS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(_hex_to_color(hexstr))
            self._token_formats[tok_type] = fmt

        self._md_pipe_format = QTextCharFormat()
        self._md_pipe_format.setForeground(_hex_to_color(MD_TABLE_PIPE))

        self._md_separator_format = QTextCharFormat()
        self._md_separator_format.setForeground(_hex_to_color(MD_TABLE_SEPARATOR))

        self._md_header_format = QTextCharFormat()
        self._md_header_format.setForeground(_hex_to_color(MD_TABLE_HEADER))
        self._md_header_format.setFontWeight(QFont.Bold)

    @property
    def md_pipe_format(self):
        return self._md_pipe_format

    @property
    def md_separator_format(self):
        return self._md_separator_format

    @property
    def md_header_format(self):
        return self._md_header_format

    def format_for_token(self, tok_type):
        """Return a QTextCharFormat for a token type, walking .parent for fallbacks."""
        t = tok_type
        while t is not None:
            fmt = self._token_formats.get(t)
            if fmt is not None:
                return fmt
            try:
                t = t.parent
            except AttributeError:
                t = None
        return None

    def stylesheet(self) -> str:
        return STYLESHEET


STYLESHEET = """
* {
    outline: none;
}
QMainWindow, QDialog {
    background-color: #1e1e1e;
    color: #d4d4d4;
}
QMenuBar {
    background-color: #252526;
    color: #cccccc;
    border-bottom: 1px solid #333333;
}
QMenuBar::item {
    padding: 6px 12px;
    background: transparent;
}
QMenuBar::item:selected {
    background: #094771;
}
QMenu {
    background-color: #2d2d2d;
    color: #cccccc;
    border: 1px solid #444444;
}
QMenu::item {
    padding: 5px 24px 5px 20px;
    background: transparent;
}
QMenu::item:selected {
    background: #094771;
    color: #ffffff;
}
QMenu::item:disabled {
    color: #666666;
}
QMenu::separator {
    height: 1px;
    background: #444444;
    margin: 4px 8px;
}
QTabWidget::pane {
    border: none;
    background: #1e1e1e;
    top: -1px;
}
QTabBar {
    background: #2d2d2d;
}
QTabBar::tab {
    background: #2d2d2d;
    color: #969696;
    padding: 8px 14px;
    border: none;
    border-right: 1px solid #1e1e1e;
    max-width: 220px;
}
QTabBar::tab:selected {
    background: #1e1e1e;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    background: #3a3a3a;
}
QPlainTextEdit, QTextEdit {
    background: #1e1e1e;
    color: #d4d4d4;
    border: none;
    selection-background-color: #264f78;
    selection-color: #ffffff;
}
QTreeView, QListView, QTreeWidget {
    background: #252526;
    color: #cccccc;
    border: none;
    alternate-background-color: #2a2a2b;
}
QTreeView::item, QListView::item {
    padding: 3px 4px;
    background: transparent;
}
QTreeView::item:selected, QListView::item:selected {
    background: #094771;
    color: #ffffff;
}
QTreeView::branch {
    background: transparent;
}
QHeaderView::section {
    background: #2d2d2d;
    color: #cccccc;
    border: none;
    padding: 4px;
}
QSplitter::handle {
    background: #333333;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}
QLineEdit {
    background: #3c3c3c;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 4px 6px;
    selection-background-color: #264f78;
}
QLineEdit:focus {
    border: 1px solid #094771;
}
QPushButton {
    background: #3c3c3c;
    color: #cccccc;
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 5px 12px;
}
QPushButton:hover {
    background: #4a4a4a;
}
QPushButton:pressed {
    background: #094771;
}
QPushButton:disabled {
    color: #666666;
    background: #303030;
}
QAbstractScrollArea {
    background: #1e1e1e;
}
QScrollArea {
    background: #1e1e1e;
    border: none;
}
QScrollBar:vertical {
    background: #1e1e1e;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #424242;
    border-radius: 5px;
    min-height: 24px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: #4f4f4f;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background: #1e1e1e;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #424242;
    border-radius: 5px;
    min-width: 24px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: #4f4f4f;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}
QToolTip {
    background-color: #2d2d2d;
    color: #cccccc;
    border: 1px solid #555555;
    padding: 4px;
}
QLabel {
    background: transparent;
    color: #d4d4d4;
}
QGroupBox {
    border: 1px solid #444444;
    border-radius: 3px;
    margin-top: 12px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
"""
