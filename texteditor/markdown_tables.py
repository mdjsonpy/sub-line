"""Pure (Qt-free) Markdown table helpers.

Provides detection of table blocks, cell splitting, column alignment, and
per-line classification (in-table / separator / header) used both by the
table syntax highlighter and the editor's table editing helpers.
"""
from __future__ import annotations

import re

_MD_EXTENSIONS = (".md", ".markdown", ".mdown", ".mdwn", ".mkd", ".mkdn", ".mdx")
_SEP_CELL_RE = re.compile(r":?-{1,}:?")


def is_markdown_path(path: str) -> bool:
    """Return True if ``path`` looks like a Markdown file by extension."""
    if not path:
        return False
    low = path.lower().rsplit("?", 1)[0].rsplit("#", 1)[0]
    return low.endswith(_MD_EXTENSIONS)


def _pipe_indices(line: str) -> list[int]:
    """Indices of unescaped ``|`` characters in ``line`` (skips ``\\|``)."""
    idxs: list[int] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n and line[i + 1] == "|":
            i += 2
            continue
        if ch == "|":
            idxs.append(i)
        i += 1
    return idxs


def split_cells(line: str) -> list[str]:
    """Split a table row into trimmed cell strings.

    A row needs at least two pipes to count as a table row; otherwise an empty
    list is returned (so inline single-pipe text is not treated as a table).
    """
    pipes = _pipe_indices(line)
    if len(pipes) < 2:
        return []
    cells: list[str] = []
    for i in range(len(pipes) - 1):
        seg = line[pipes[i] + 1: pipes[i + 1]]
        cells.append(seg.strip())
    return cells


def cell_spans(line: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` line-local offsets of each cell's content.

    ``start`` is the first non-space char after a pipe, ``end`` is just past the
    last non-space char before the next pipe. Used to place the cursor inside a
    cell without eating its padding.
    """
    pipes = _pipe_indices(line)
    if len(pipes) < 2:
        return []
    spans: list[tuple[int, int]] = []
    for i in range(len(pipes) - 1):
        a = pipes[i] + 1
        b = pipes[i + 1]
        s = a
        while s < b and line[s] == " ":
            s += 1
        e = b
        while e > a and line[e - 1] == " ":
            e -= 1
        spans.append((s, e))
    return spans


def is_table_row(line: str) -> bool:
    """True if the line is a Markdown table row (>= 2 pipes forming a cell)."""
    return len(split_cells(line)) >= 1


def is_separator_row(line: str) -> bool:
    """True if the line is a ``| --- | :---: | ---: |`` separator row."""
    if "-" not in line or not is_table_row(line):
        return False
    cells = split_cells(line)
    return all(_SEP_CELL_RE.fullmatch(c) for c in cells)


def _fence_flags(lines: list[str]) -> list[bool]:
    """Per-line flag: True if the line is inside (or is) a ``` / ~~~ code fence."""
    flags: list[bool] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flags.append(True)
            in_fence = not in_fence
        else:
            flags.append(in_fence)
    return flags


def find_table_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Return ``[(start, end)]`` (inclusive line indices) of table blocks.

    A block is a run of >= 2 consecutive table rows not inside a code fence.
    """
    in_fence = _fence_flags(lines)
    n = len(lines)
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not in_fence[i] and is_table_row(lines[i]):
            j = i
            while j + 1 < n and not in_fence[j + 1] and is_table_row(lines[j + 1]):
                j += 1
            if j > i:
                blocks.append((i, j))
            i = j + 1
        else:
            i += 1
    return blocks


def _pad_separator(cell: str, width: int) -> str:
    """Rebuild a separator cell of exactly ``width`` chars, preserving alignment.

    ``:`` on the left = left-align, right = right-align, both = center, none =
    default. The result is always at least 3 dashes wide when ``width >= 3``.
    """
    cell = cell.strip()
    left = cell.startswith(":")
    right = cell.endswith(":")
    if left and right:
        return ":" + "-" * max(1, width - 2) + ":"
    if left:
        return ":" + "-" * max(1, width - 1)
    if right:
        return "-" * max(1, width - 1) + ":"
    return "-" * width


def align_block(lines: list[str], start: int, end: int) -> list[str]:
    """Return the lines ``[start..end]`` re-aligned into a clean column grid."""
    rows = []
    for i in range(start, end + 1):
        rows.append((i, split_cells(lines[i])))
    ncols = max((len(cells) for _, cells in rows if cells), default=0)
    if ncols == 0:
        return list(lines[start:end + 1])

    widths = [0] * ncols
    for i, cells in rows:
        is_sep = is_separator_row(lines[i])
        for idx, cell in enumerate(cells):
            if idx >= ncols:
                break
            w = len(cell)
            if is_sep:
                w = max(w, 3)
            if w > widths[idx]:
                widths[idx] = w
    widths = [max(1, w) for w in widths]

    out: list[str] = []
    for i, cells in rows:
        line = lines[i]
        if not cells:
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        is_sep = is_separator_row(line)
        parts = []
        for idx in range(ncols):
            cell = cells[idx] if idx < len(cells) else ""
            if is_sep:
                parts.append(_pad_separator(cell, widths[idx]))
            else:
                parts.append(cell.ljust(widths[idx]))
        out.append(indent + "| " + " | ".join(parts) + " |")
    return out


def align_all(lines: list[str]) -> tuple[list[str], bool]:
    """Align every table block in ``lines``; return ``(new_lines, changed)``."""
    blocks = find_table_blocks(lines)
    if not blocks:
        return lines, False
    out = list(lines)
    changed = False
    for s, e in blocks:
        new_rows = align_block(lines, s, e)
        if new_rows != lines[s:e + 1]:
            changed = True
        out[s:e + 1] = new_rows
    return out, changed


def line_flags(lines: list[str]) -> tuple[list[bool], list[bool], list[bool]]:
    """Per-line ``(in_table, is_separator, is_header)`` booleans for highlighting."""
    n = len(lines)
    in_table = [False] * n
    for s, e in find_table_blocks(lines):
        for i in range(s, e + 1):
            in_table[i] = True
    is_sep = [in_table[i] and is_separator_row(lines[i]) for i in range(n)]
    is_header = [
        in_table[i] and not is_sep[i] and i + 1 < n and is_sep[i + 1]
        for i in range(n)
    ]
    return in_table, is_sep, is_header
