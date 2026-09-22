"""Persistence of named workspaces to a JSON file.

Location: ``$XDG_CONFIG_HOME/texteditor/workspaces.json``
(defaulting to ``~/.config/texteditor/workspaces.json``).

The store now also persists the visual layout (categories + ordering), the
collapsed state of category headers, the selected (active) workspace, global
view preferences (``settings``), and the window size + main splitter ratio
(``window``). ``load`` returns a ``StoreData`` bundle; ``save`` accepts one.
``version:1`` (workspaces only) and ``version:2`` (layout + collapsed) files are
migrated to the ``version:3`` shape on load, filling in defaults.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field, asdict


@dataclass
class Workspace:
    id: str
    name: str | None = None
    root: str | None = None
    tabs: list = field(default_factory=list)  # list[str] absolute paths
    active: int = 0
    splits: list = field(default_factory=list)  # list[list[str]] per split group
    split_actives: list = field(default_factory=list)  # active index per group


@dataclass
class LayoutEntry:
    kind: str  # "category" | "workspace"
    ref: str  # category name, OR workspace id


DEFAULT_SETTINGS: dict = {
    "wrap": True,
    "show_whitespace": False,
    "zoom": 0,
    "sidebar": True,
}

DEFAULT_WINDOW: dict = {
    "width": 1100,
    "height": 720,
    "main_split": [200, 900],
}


@dataclass
class StoreData:
    workspaces: list  # list[Workspace] (unchanged fields)
    layout: list  # list[LayoutEntry] (ordered visual structure)
    collapsed: list = field(default_factory=list)  # list[str] category names
    active_workspace_id: str | None = None
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    window: dict = field(default_factory=lambda: dict(DEFAULT_WINDOW))


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "texteditor")


def store_path() -> str:
    return os.path.join(config_dir(), "workspaces.json")


def _parse_workspace(raw: dict) -> Workspace | None:
    try:
        splits_raw = raw.get("splits") or []
        splits = [
            [str(t) for t in split]
            for split in splits_raw
            if isinstance(split, list)
        ]
        actives_raw = raw.get("split_actives") or []
        ws = Workspace(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "Workspace")),
            root=raw.get("root"),
            tabs=[str(t) for t in raw.get("tabs", [])],
            active=int(raw.get("active", 0)),
            splits=splits,
            split_actives=[
                int(a) for a in actives_raw
                if isinstance(a, (int, float))
            ],
        )
    except (TypeError, ValueError):
        return None
    if not ws.id:
        return None
    return ws


def _parse_workspaces(data: dict) -> list[Workspace]:
    out: list[Workspace] = []
    for raw in data.get("workspaces", []):
        if not isinstance(raw, dict):
            continue
        ws = _parse_workspace(raw)
        if ws is not None:
            out.append(ws)
    return out


def _parse_layout(data: dict, valid_ids: set) -> list[LayoutEntry]:
    layout: list[LayoutEntry] = []
    for raw in data.get("layout", []):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        ref = raw.get("ref")
        if not isinstance(ref, str) or not ref:
            continue
        if kind == "workspace":
            if ref in valid_ids:
                layout.append(LayoutEntry("workspace", ref))
        elif kind == "category":
            layout.append(LayoutEntry("category", ref))
    return layout


def _parse_settings(raw) -> dict:
    out = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        if isinstance(raw.get("wrap"), bool):
            out["wrap"] = raw["wrap"]
        if isinstance(raw.get("show_whitespace"), bool):
            out["show_whitespace"] = raw["show_whitespace"]
        if isinstance(raw.get("zoom"), (int, float)) and not isinstance(raw.get("zoom"), bool):
            out["zoom"] = int(raw["zoom"])
        if isinstance(raw.get("sidebar"), bool):
            out["sidebar"] = raw["sidebar"]
    return out


def _parse_window(raw) -> dict:
    out = dict(DEFAULT_WINDOW)
    out["main_split"] = list(DEFAULT_WINDOW["main_split"])
    if isinstance(raw, dict):
        for key in ("width", "height"):
            v = raw.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                out[key] = int(v)
        ms = raw.get("main_split")
        if (
            isinstance(ms, list)
            and len(ms) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in ms)
        ):
            out["main_split"] = [int(ms[0]), int(ms[1])]
    return out


def _resolve_active_id(data: dict, layout: list[LayoutEntry], valid_ids: set) -> str | None:
    active = data.get("active_workspace_id")
    if isinstance(active, str) and active in valid_ids:
        return active
    # fall back to the first workspace present in the layout
    for entry in layout:
        if entry.kind == "workspace" and entry.ref in valid_ids:
            return entry.ref
    return None


class WorkspacesStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or store_path()

    def load(self) -> StoreData:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return StoreData([], [])
        if not isinstance(data, dict):
            return StoreData([], [])

        workspaces = _parse_workspaces(data)
        valid_ids = {ws.id for ws in workspaces}

        if "layout" in data:
            layout = _parse_layout(data, valid_ids)
            collapsed = [
                str(c) for c in data.get("collapsed", []) if isinstance(c, str)
            ]
        else:
            # version:1 file — synthesize a flat layout, original order preserved
            layout = [LayoutEntry("workspace", ws.id) for ws in workspaces]
            collapsed = []

        return StoreData(
            workspaces=workspaces,
            layout=layout,
            collapsed=collapsed,
            active_workspace_id=_resolve_active_id(data, layout, valid_ids),
            settings=_parse_settings(data.get("settings")),
            window=_parse_window(data.get("window")),
        )

    def save(self, data: StoreData) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "version": 3,
            "workspaces": [asdict(ws) for ws in data.workspaces],
            "layout": [{"kind": e.kind, "ref": e.ref} for e in data.layout],
            "collapsed": list(data.collapsed),
            "active_workspace_id": data.active_workspace_id,
            "settings": dict(data.settings),
            "window": dict(data.window),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)
