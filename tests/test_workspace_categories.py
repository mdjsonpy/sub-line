"""Tests for workspace categories: v2 persistence, migration, and list operations.

Covers: v2 round-trip, v1->v2 migration, category create/rename/reorder,
workspace move up/down (same group only), move-to-category, and
delete-category (closes members, preserves files).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

import texteditor.main_window as mw_mod
from texteditor.store import Workspace, WorkspacesStore, LayoutEntry, StoreData

app = None


def qapp() -> QApplication:
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_main_window(tmp_path, workspaces, layout, collapsed=()):
    qapp()
    from texteditor.main_window import MainWindow
    from texteditor.theme import Theme

    store = WorkspacesStore(os.fspath(tmp_path) + os.sep + "ws.json")
    store.save(StoreData(workspaces, layout, list(collapsed)))
    return MainWindow(Theme(), store)


def ws(id_, root, name=None):
    return Workspace(id=id_, name=name or id_, root=root, tabs=[], active=0)


class TestStoreV2:
    def test_v2_roundtrip(self, tmp_path):
        path = os.fspath(tmp_path) + os.sep + "ws.json"
        store = WorkspacesStore(path)
        data = StoreData(
            workspaces=[
                Workspace(id="w1", name="One", root="/a", tabs=["/a/x.py"], active=0),
                Workspace(id="w2", name="Two", root="/b", tabs=[], active=0),
            ],
            layout=[
                LayoutEntry("workspace", "w1"),
                LayoutEntry("category", "Work"),
                LayoutEntry("workspace", "w2"),
            ],
            collapsed=["Work"],
        )
        store.save(data)

        raw = json.load(open(path, encoding="utf-8"))
        assert raw["version"] >= 4
        assert raw["layout"] == [
            {"kind": "workspace", "ref": "w1"},
            {"kind": "category", "ref": "Work"},
            {"kind": "workspace", "ref": "w2"},
        ]
        assert raw["collapsed"] == ["Work"]

        loaded = WorkspacesStore(path).load()
        assert [w.id for w in loaded.workspaces] == ["w1", "w2"]
        assert [(e.kind, e.ref) for e in loaded.layout] == [
            ("workspace", "w1"), ("category", "Work"), ("workspace", "w2"),
        ]
        assert loaded.collapsed == ["Work"]

    def test_v1_migration(self, tmp_path):
        path = os.fspath(tmp_path) + os.sep + "ws.json"
        v1 = {
            "version": 1,
            "workspaces": [
                {"id": "a", "name": "A", "root": "/x", "tabs": ["/x/a.py"], "active": 0},
                {"id": "b", "name": "B", "root": "/y", "tabs": [], "active": 0},
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(v1, fh)

        loaded = WorkspacesStore(path).load()
        assert [w.id for w in loaded.workspaces] == ["a", "b"]
        assert [(e.kind, e.ref) for e in loaded.layout] == [
            ("workspace", "a"), ("workspace", "b"),
        ]
        assert loaded.collapsed == []

    def test_layout_drops_unknown_workspace_ids(self, tmp_path):
        path = os.fspath(tmp_path) + os.sep + "ws.json"
        payload = {
            "version": 2,
            "workspaces": [{"id": "a", "name": "A", "root": "/x", "tabs": [], "active": 0}],
            "layout": [
                {"kind": "workspace", "ref": "a"},
                {"kind": "workspace", "ref": "ghost"},
                {"kind": "category", "ref": "Keep"},
            ],
            "collapsed": ["Keep"],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

        loaded = WorkspacesStore(path).load()
        assert [(e.kind, e.ref) for e in loaded.layout] == [
            ("workspace", "a"), ("category", "Keep"),
        ]

    def test_missing_file_is_empty(self, tmp_path):
        loaded = WorkspacesStore(os.fspath(tmp_path) + os.sep + "nope.json").load()
        assert loaded.workspaces == [] and loaded.layout == [] and loaded.collapsed == []


class TestCategoryCreateRename:
    def test_new_category_appends(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)], [LayoutEntry("workspace", "a")]
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("Work", True)),
            )
            mw.new_category()
            assert mw._category_names() == ["Work"]
            assert mw._layout[-1] == LayoutEntry("category", "Work")
            # persisted under v2
            mw._save_state()
            reloaded = WorkspacesStore(mw._store.path).load()
            assert LayoutEntry("category", "Work") in reloaded.layout
        finally:
            mw.close()

    def test_new_category_rejects_blank_and_duplicate(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)],
            [LayoutEntry("category", "Work"), LayoutEntry("workspace", "a")],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("   ", True)),
            )
            mw.new_category()
            assert mw._category_names() == ["Work"]

            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("Work", True)),
            )
            mw.new_category()
            assert mw._category_names() == ["Work"]
        finally:
            mw.close()

    def test_new_category_cancelled(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)], [LayoutEntry("workspace", "a")]
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("Work", False)),
            )
            mw.new_category()
            assert mw._category_names() == []
        finally:
            mw.close()

    def test_rename_category_keeps_members_and_collapse(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)],
            [LayoutEntry("category", "Work"), LayoutEntry("workspace", "a")],
            collapsed=["Work"],
        )
        try:
            assert mw._collapsed == {"Work"}
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("Work2", True)),
            )
            mw.rename_category("Work")
            assert mw._category_names() == ["Work2"]
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("category", "Work2"), ("workspace", "a"),
            ]
            assert mw._collapsed == {"Work2"}
        finally:
            mw.close()

    def test_rename_category_rejects_duplicate(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)],
            [
                LayoutEntry("category", "A"), LayoutEntry("workspace", "a"),
                LayoutEntry("category", "B"),
            ],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getText",
                staticmethod(lambda *a, **k: ("B", True)),
            )
            mw.rename_category("A")
            assert mw._category_names() == ["A", "B"]
        finally:
            mw.close()


class TestCategoryReorder:
    def _mw(self, tmp_path):
        root = os.fspath(tmp_path)
        return make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [
                LayoutEntry("category", "First"), LayoutEntry("workspace", "a"),
                LayoutEntry("category", "Second"), LayoutEntry("workspace", "b"),
            ],
        )

    def test_move_category_down_swaps_blocks(self, tmp_path):
        mw = self._mw(tmp_path)
        try:
            mw.move_category_down("First")
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("category", "Second"), ("workspace", "b"),
                ("category", "First"), ("workspace", "a"),
            ]
        finally:
            mw.close()

    def test_move_category_up_swaps_blocks(self, tmp_path):
        mw = self._mw(tmp_path)
        try:
            mw.move_category_up("Second")
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("category", "Second"), ("workspace", "b"),
                ("category", "First"), ("workspace", "a"),
            ]
            # back
            mw.move_category_down("Second")
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("category", "First"), ("workspace", "a"),
                ("category", "Second"), ("workspace", "b"),
            ]
        finally:
            mw.close()

    def test_move_category_edge_is_noop(self, tmp_path):
        mw = self._mw(tmp_path)
        try:
            before = [(e.kind, e.ref) for e in mw._layout]
            mw.move_category_up("First")   # no category above
            mw.move_category_down("Second")  # no category below
            assert [(e.kind, e.ref) for e in mw._layout] == before
        finally:
            mw.close()


class TestWorkspaceMove:
    def test_move_down_within_uncategorized(self, tmp_path):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [LayoutEntry("workspace", "a"), LayoutEntry("workspace", "b")],
        )
        try:
            mw.move_workspace_down("a")
            assert [e.ref for e in mw._layout] == ["b", "a"]
            mw.move_workspace_up("a")
            assert [e.ref for e in mw._layout] == ["a", "b"]
        finally:
            mw.close()

    def test_move_down_within_category(self, tmp_path):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [
                LayoutEntry("category", "Work"),
                LayoutEntry("workspace", "a"),
                LayoutEntry("workspace", "b"),
            ],
        )
        try:
            mw.move_workspace_down("a")
            assert [e.ref for e in mw._layout if e.kind == "workspace"] == ["b", "a"]
        finally:
            mw.close()

    def test_move_does_not_cross_category_boundary(self, tmp_path):
        # Convention: a workspace belongs to the nearest preceding category.
        # ``b`` is top-level (before the category), ``a`` is inside Work.
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [
                LayoutEntry("workspace", "b"),
                LayoutEntry("category", "Work"), LayoutEntry("workspace", "a"),
            ],
        )
        try:
            before = [(e.kind, e.ref) for e in mw._layout]
            mw.move_workspace_down("b")  # next entry is a category -> no-op
            assert [(e.kind, e.ref) for e in mw._layout] == before
            mw.move_workspace_up("a")    # different group -> disallowed
            assert [(e.kind, e.ref) for e in mw._layout] == before
        finally:
            mw.close()

    def test_move_to_category(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [LayoutEntry("workspace", "a"), LayoutEntry("category", "Work"),
             LayoutEntry("workspace", "b")],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getItem",
                staticmethod(lambda *a, **k: ("Work", True)),
            )
            mw.move_workspace_to_category("a")
            # appended at the end of the category's block
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("category", "Work"), ("workspace", "b"), ("workspace", "a"),
            ]
        finally:
            mw.close()

    def test_move_to_uncategorized(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [LayoutEntry("category", "Work"), LayoutEntry("workspace", "a"),
             LayoutEntry("workspace", "b")],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QInputDialog, "getItem",
                staticmethod(lambda *a, **k: ("Uncategorized", True)),
            )
            mw.move_workspace_to_category("a")
            assert [(e.kind, e.ref) for e in mw._layout] == [
                ("workspace", "a"), ("category", "Work"), ("workspace", "b"),
            ]
        finally:
            mw.close()


class TestDeleteCategory:
    def test_delete_closes_members_keeps_files(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        fa = os.path.join(root, "a.txt")
        with open(fa, "w", encoding="utf-8") as fh:
            fh.write("keep me")
        # ``b`` is top-level (before the category); ``a`` is inside Work.
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [LayoutEntry("workspace", "b"), LayoutEntry("category", "Work"),
             LayoutEntry("workspace", "a")],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QMessageBox, "question",
                staticmethod(lambda *a, **k: mw_mod.QMessageBox.Yes),
            )
            mw.delete_category("Work")
            assert [w.id for w in mw._workspaces] == ["b"]
            assert [(e.kind, e.ref) for e in mw._layout] == [("workspace", "b")]
            assert os.path.exists(fa)  # file preserved
        finally:
            mw.close()

    def test_delete_keeps_nonmember_current(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root), ws("b", root)],
            [LayoutEntry("workspace", "b"), LayoutEntry("category", "Work"),
             LayoutEntry("workspace", "a")],
        )
        try:
            # activate a (the member) so it is current at delete time
            mw.activate_workspace("a")
            assert mw.current_workspace().id == "a"
            monkeypatch.setattr(
                mw_mod.QMessageBox, "question",
                staticmethod(lambda *a, **k: mw_mod.QMessageBox.Yes),
            )
            mw.delete_category("Work")
            assert [w.id for w in mw._workspaces] == ["b"]
            assert mw.current_workspace().id == "b"
        finally:
            mw.close()

    def test_delete_declined_is_noop(self, tmp_path, monkeypatch):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)],
            [LayoutEntry("category", "Work"), LayoutEntry("workspace", "a")],
        )
        try:
            monkeypatch.setattr(
                mw_mod.QMessageBox, "question",
                staticmethod(lambda *a, **k: mw_mod.QMessageBox.No),
            )
            mw.delete_category("Work")
            assert [w.id for w in mw._workspaces] == ["a"]
            assert mw._category_names() == ["Work"]
        finally:
            mw.close()

    def test_delete_unknown_category_is_noop(self, tmp_path):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)], [LayoutEntry("workspace", "a")]
        )
        try:
            mw.delete_category("DoesNotExist")
            assert [w.id for w in mw._workspaces] == ["a"]
        finally:
            mw.close()


class TestEncryptedActivation:
    def test_ensure_unlocked_already_unlocked_no_raise(self, tmp_path):
        """Regression: ``_ensure_unlocked`` on an already-unlocked vault must
        not raise and must point ``ws.root`` at the decrypted temp dir.

        Previously it called ``VaultManager.temp_dir_for(ws_id)``, which does
        not exist — the per-vault temp dir is exposed as ``Vault.temp_path``.
        This is the exact line from the reported traceback.
        """
        import uuid
        from texteditor import crypto
        from texteditor.vault import VaultManager

        # A real encrypted vault created from a folder with content.
        folder = tmp_path / "src"
        folder.mkdir()
        (folder / "hello.py").write_text("print('hi')\n")
        vault_file = crypto.create_encrypted_workspace(os.fspath(folder), "pw")
        assert os.path.isfile(vault_file)
        assert not (folder / "hello.py").exists()  # plaintext purged

        ws_id = uuid.uuid4().hex
        enc_ws = Workspace(
            id=ws_id, name="Enc", root="", tabs=[], active=0,
            encrypted=True, vault_path=vault_file,
        )
        mw = make_main_window(
            tmp_path, [enc_ws], [LayoutEntry("workspace", ws_id)]
        )
        try:
            # Isolated vault manager under tmp_path (don't pollute the real
            # ~/.local/share/subline data dir).
            mw._vaults = VaultManager(os.fspath(tmp_path) + os.sep + "vaults")
            mw._vaults.get(ws_id, vault_file).unlock("pw")
            assert mw._vaults.get(ws_id, vault_file).is_unlocked

            # The exact line that raised before the fix:
            ok = mw._ensure_unlocked(enc_ws)
            assert ok is True
            assert enc_ws.root and os.path.isdir(enc_ws.root)
            assert os.path.isfile(os.path.join(enc_ws.root, "hello.py"))
        finally:
            mw.close()


class TestCollapsePersistence:
    def test_collapse_state_saved_from_tree(self, tmp_path):
        root = os.fspath(tmp_path)
        mw = make_main_window(
            tmp_path, [ws("a", root)],
            [LayoutEntry("category", "Work"), LayoutEntry("workspace", "a")],
        )
        try:
            item = mw._ws_list.tree.topLevelItem(0)
            assert item.isExpanded()
            item.setExpanded(False)
            mw._save_state()
            reloaded = WorkspacesStore(mw._store.path).load()
            assert reloaded.collapsed == ["Work"]
        finally:
            mw.close()
