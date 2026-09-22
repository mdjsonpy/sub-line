"""Todos data model + JSON persistence (object-map format).

A ``Todos`` document is an ordered mapping of category name -> three bucket
lists (``todo`` / ``doing`` / ``done``), each a list of ``Task``. A task is
exactly ``{"text": <non-empty str>, "priority": <int 1..3>}`` and lives in
exactly one bucket.

Pure data + validation, no Qt imports. Fully unit-testable headlessly.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

BUCKETS = ("todo", "doing", "done")
BUCKET_LABELS = {"todo": "Todo", "doing": "Doing", "done": "Done"}
PRIORITIES = (1, 2, 3)
DEFAULT_PRIORITY = 2

# Priority value -> (label, theme color). Colors come from theme.py.
PRIORITY_META = {
    1: ("Low", "#4ec9b0"),
    2: ("Medium", "#d7ba7d"),
    3: ("High", "#cd3131"),
}


@dataclass
class Task:
    text: str
    priority: int = DEFAULT_PRIORITY


class TodoError(ValueError):
    """Raised on invalid Todos mutations (bad name, bad bucket, etc.)."""


def _clamp_priority(p: int) -> int:
    try:
        p = int(p)
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return max(1, min(3, p))


class Todos:
    def __init__(self) -> None:
        # ordered dict: category name -> {bucket: list[Task]}
        self._categories: dict[str, dict[str, list[Task]]] = {}

    # ---- categories ----------------------------------------------------------
    def add_category(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise TodoError("Category name is required")
        if name in self._categories:
            raise TodoError(f"Category '{name}' already exists")
        self._categories[name] = {b: [] for b in BUCKETS}

    def rename_category(self, old: str, new: str) -> None:
        if old not in self._categories:
            raise TodoError(old)
        new = (new or "").strip()
        if not new or new in self._categories:
            raise TodoError("Invalid or duplicate category name")
        # Rebuild the dict in place so the renamed category keeps its position
        # (a plain pop + re-insert would move it to the end).
        pos = list(self._categories.keys()).index(old)
        buckets = self._categories.pop(old)
        items = list(self._categories.items())
        items.insert(pos, (new, buckets))
        self._categories = dict(items)

    def delete_category(self, name: str) -> None:
        self._categories.pop(name, None)

    def categories(self) -> list[str]:
        return list(self._categories.keys())

    def task_total(self, cat: str) -> int:
        if cat not in self._categories:
            return 0
        return sum(len(v) for v in self._categories[cat].values())

    # ---- tasks ---------------------------------------------------------------
    def _bucket(self, cat: str, bucket: str) -> list[Task]:
        if cat not in self._categories:
            raise TodoError(cat)
        if bucket not in BUCKETS:
            raise TodoError(bucket)
        return self._categories[cat][bucket]

    def tasks(self, cat: str, bucket: str) -> list[Task]:
        return list(self._bucket(cat, bucket))

    def add_task(self, cat: str, bucket: str, text: str, priority: int = DEFAULT_PRIORITY) -> None:
        text = (text or "").strip()
        if not text:
            raise TodoError("Task text is required")
        self._bucket(cat, bucket).append(Task(text, _clamp_priority(priority)))

    def update_task(self, cat: str, bucket: str, index: int, text: str, priority: int) -> None:
        lst = self._bucket(cat, bucket)
        if not (0 <= index < len(lst)):
            raise TodoError("index")
        new_text = (text or "").strip() or lst[index].text
        lst[index] = Task(new_text, _clamp_priority(priority))

    def delete_tasks(self, cat: str, bucket: str, indices: list[int]) -> None:
        lst = self._bucket(cat, bucket)
        for i in sorted(set(i for i in indices if 0 <= i < len(lst)), reverse=True):
            del lst[i]

    def move_tasks(self, src_cat: str, src_bucket: str, indices: list[int],
                   dst_cat: str, dst_bucket: str) -> None:
        if src_cat == dst_cat and src_bucket == dst_bucket:
            return  # no-op
        # Validate BOTH endpoints before mutating so a bad destination can
        # never leave the source list drained (data loss).
        src = self._bucket(src_cat, src_bucket)
        dst = self._bucket(dst_cat, dst_bucket)
        valid = sorted({i for i in indices if 0 <= i < len(src)}, reverse=True)
        if not valid:
            return
        moved = [src[i] for i in sorted(valid, reverse=True)]
        moved.reverse()  # preserve original relative order
        for i in valid:
            del src[i]
        dst.extend(moved)


# ---- serialization -----------------------------------------------------------
def todos_to_dict(t: Todos) -> dict:
    return {
        "version": 1,
        "categories": {
            name: {b: [{"text": task.text, "priority": task.priority}
                       for task in t._categories[name][b]] for b in BUCKETS}
            for name in t._categories
        },
    }


def todos_from_dict(data: dict) -> Todos:
    t = Todos()
    cats = data.get("categories", {}) if isinstance(data, dict) else {}
    if not isinstance(cats, dict):
        return t
    for name, buckets in cats.items():
        if not isinstance(buckets, dict):
            continue
        entry = {}
        for b in BUCKETS:
            tasks = []
            for raw in (buckets.get(b) or []):
                if isinstance(raw, dict) and str(raw.get("text", "")).strip():
                    tasks.append(
                        Task(str(raw["text"]).strip(),
                             _clamp_priority(raw.get("priority", DEFAULT_PRIORITY)))
                    )
            entry[b] = tasks
        t._categories[str(name)] = entry
    return t


class TodosStore:
    def load(self, path: str) -> Todos:
        with open(path, "r", encoding="utf-8") as fh:
            return todos_from_dict(json.load(fh))

    def save(self, t: Todos, path: str) -> None:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(todos_to_dict(t), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)  # atomic write, like store.py
