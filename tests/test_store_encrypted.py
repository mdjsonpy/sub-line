"""Tests for encrypted-workspace persistence: v4 roundtrip, v3->v4
migration (encrypted defaults to False), and the encrypted flag in the
saved JSON.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texteditor.store import Workspace, WorkspacesStore, LayoutEntry, StoreData


def store_path(tmp_path) -> str:
    return os.fspath(tmp_path) + os.sep + "ws.json"


class TestEncryptedRoundtrip:
    def test_v4_roundtrip(self, tmp_path):
        path = store_path(tmp_path)
        store = WorkspacesStore(path)
        data = StoreData(
            workspaces=[
                Workspace(id="w1", name="Plain", root="/a", tabs=["/a/x.py"], active=0),
                Workspace(id="w2", name="Enc", root="/a/enc.vault", tabs=[],
                          active=0, encrypted=True),
            ],
            layout=[LayoutEntry("workspace", "w1"), LayoutEntry("workspace", "w2")],
        )
        store.save(data)

        loaded = WorkspacesStore(path).load()
        assert [w.id for w in loaded.workspaces] == ["w1", "w2"]
        assert loaded.workspaces[0].encrypted is False
        assert loaded.workspaces[1].encrypted is True
        assert loaded.workspaces[1].root == "/a/enc.vault"

    def test_encrypted_field_in_json(self, tmp_path):
        path = store_path(tmp_path)
        WorkspacesStore(path).save(StoreData(
            workspaces=[Workspace(id="w1", name="E", root="/x.vault",
                                  tabs=[], active=0, encrypted=True)],
            layout=[LayoutEntry("workspace", "w1")],
        ))
        raw = json.load(open(path, encoding="utf-8"))
        assert raw["version"] == 4
        assert raw["workspaces"][0]["encrypted"] is True

    def test_explicit_false_preserved(self, tmp_path):
        path = store_path(tmp_path)
        WorkspacesStore(path).save(StoreData(
            workspaces=[Workspace(id="w1", name="P", root="/a",
                                  tabs=[], active=0, encrypted=False)],
            layout=[LayoutEntry("workspace", "w1")],
        ))
        raw = json.load(open(path, encoding="utf-8"))
        assert raw["workspaces"][0]["encrypted"] is False
        loaded = WorkspacesStore(path).load()
        assert loaded.workspaces[0].encrypted is False


class TestMigration:
    def _write(self, path, version, ws_raw):
        payload = {
            "version": version,
            "workspaces": [ws_raw],
            "layout": [{"kind": "workspace", "ref": "w1"}],
            "collapsed": [],
            "active_workspace_id": "w1",
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_v3_defaults_encrypted_false(self, tmp_path):
        path = store_path(tmp_path)
        self._write(path, 3, {
            "id": "w1", "name": "Old", "root": "/a", "tabs": ["/a/f.py"],
            "active": 0, "splits": [], "split_actives": [],
        })
        loaded = WorkspacesStore(path).load()
        assert loaded.workspaces[0].encrypted is False

    def test_v2_defaults_encrypted_false(self, tmp_path):
        path = store_path(tmp_path)
        self._write(path, 2, {
            "id": "w1", "name": "Old", "root": "/a", "tabs": [], "active": 0,
        })
        loaded = WorkspacesStore(path).load()
        assert loaded.workspaces[0].encrypted is False

    def test_v1_defaults_encrypted_false(self, tmp_path):
        # v1 has no "layout" key at all
        path = store_path(tmp_path)
        payload = {
            "version": 1,
            "workspaces": [
                {"id": "w1", "name": "Old", "root": "/a", "tabs": [], "active": 0},
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        loaded = WorkspacesStore(path).load()
        assert loaded.workspaces[0].encrypted is False

    def test_garbage_encrypted_value_ignored(self, tmp_path):
        path = store_path(tmp_path)
        self._write(path, 4, {
            "id": "w1", "name": "X", "root": "/a", "tabs": [], "active": 0,
            "encrypted": "yes",  # not a bool -> treat as False
        })
        loaded = WorkspacesStore(path).load()
        assert loaded.workspaces[0].encrypted is False
