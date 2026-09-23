"""Tests for encrypted-workspace UI in WorkspaceList: red styling, padlock
label prefix, vault context-menu actions (Unlock / Lock / Change Password),
suppression of file-path actions for encrypted entries, and the new signals.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from texteditor.store import Workspace, LayoutEntry
from texteditor.workspace_list import WorkspaceList, ROLE_ENCRYPTED

app = None


def qapp() -> QApplication:
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_list() -> WorkspaceList:
    qapp()
    lst = WorkspaceList()
    workspaces = [
        Workspace(id="w1", name="Plain", root="/a", tabs=[], active=0),
        Workspace(id="w2", name="Secret", root="/a/s.vault", tabs=[],
                  active=0, encrypted=True),
    ]
    by_id = {w.id: w for w in workspaces}
    layout = [LayoutEntry("workspace", w.id) for w in workspaces]
    lst.set_model(layout, by_id, set(), None)
    return lst


def build(lst: WorkspaceList, item):
    """Build the workspace menu non-interactively (no blocking exec)."""
    menu, acts, ws_id, root = lst._build_workspace_menu(item)
    return acts, ws_id, root


class TestEncryptedRendering:
    def test_role_set(self):
        lst = make_list()
        assert lst.tree.topLevelItem(0).data(0, ROLE_ENCRYPTED) is False
        assert lst.tree.topLevelItem(1).data(0, ROLE_ENCRYPTED) is True

    def test_red_foreground(self):
        lst = make_list()
        c = lst.tree.topLevelItem(1).foreground(0).color()
        assert (c.red(), c.green(), c.blue()) == (0xE0, 0x60, 0x60)

    def test_plain_not_red(self):
        lst = make_list()
        c = lst.tree.topLevelItem(0).foreground(0).color()
        assert (c.red(), c.green(), c.blue()) != (0xE0, 0x60, 0x60)

    def test_padlock_prefix(self):
        lst = make_list()
        text = lst.tree.topLevelItem(1).text(0)
        assert "\U0001F512" in text
        assert "Secret" in text
        assert "\U0001F512" not in lst.tree.topLevelItem(0).text(0)

    def test_active_marker_keeps_padlock(self):
        lst = make_list()
        lst.set_active("w2")
        text = lst.tree.topLevelItem(1).text(0)
        assert text.startswith("✓")
        assert "\U0001F512" in text
        # plain item active marker unchanged
        lst.set_active("w1")
        assert lst.tree.topLevelItem(0).text(0) == "✓ Plain"


class TestEncryptedMenu:
    def test_vault_actions_present(self):
        lst = make_list()
        acts, _, _ = build(lst, lst.tree.topLevelItem(1))
        assert "unlock" in acts and acts["unlock"].text() == "Unlock…"
        assert "lock" in acts and acts["lock"].text() == "Lock"
        assert "chpw" in acts and acts["chpw"].text() == "Change Password…"

    def test_file_actions_hidden_for_encrypted(self):
        lst = make_list()
        acts, _, _ = build(lst, lst.tree.topLevelItem(1))
        assert "copy" not in acts
        assert "open" not in acts
        assert "terminal" not in acts

    def test_file_actions_present_for_plain(self):
        lst = make_list()
        acts, _, _ = build(lst, lst.tree.topLevelItem(0))
        assert "copy" in acts and acts["copy"].text() == "Copy Absolute Path"
        assert "open" in acts and acts["open"].text() == "Open in File Explorer"
        assert "terminal" in acts and acts["terminal"].text() == "Open Terminal Here"
        assert "unlock" not in acts
        assert "chpw" not in acts

    def test_unlock_emits_signal(self):
        lst = make_list()
        emitted = []
        lst.unlockRequested.connect(lambda w: emitted.append(("unlock", w)))
        lst.lockRequested.connect(lambda w: emitted.append(("lock", w)))
        lst.changePasswordRequested.connect(lambda w: emitted.append(("chpw", w)))
        acts, ws_id, root = build(lst, lst.tree.topLevelItem(1))
        lst._dispatch_workspace(acts, ws_id, root, acts["unlock"])
        assert emitted == [("unlock", "w2")]

    def test_lock_emits_signal(self):
        lst = make_list()
        emitted = []
        lst.lockRequested.connect(lambda w: emitted.append(("lock", w)))
        acts, ws_id, root = build(lst, lst.tree.topLevelItem(1))
        lst._dispatch_workspace(acts, ws_id, root, acts["lock"])
        assert emitted == [("lock", "w2")]

    def test_change_password_emits_signal(self):
        lst = make_list()
        emitted = []
        lst.changePasswordRequested.connect(lambda w: emitted.append(("chpw", w)))
        acts, ws_id, root = build(lst, lst.tree.topLevelItem(1))
        lst._dispatch_workspace(acts, ws_id, root, acts["chpw"])
        assert emitted == [("chpw", "w2")]

    def test_dispatch_none_is_noop(self):
        lst = make_list()
        emitted = []
        lst.unlockRequested.connect(lambda w: emitted.append(("unlock", w)))
        acts, ws_id, root = build(lst, lst.tree.topLevelItem(1))
        lst._dispatch_workspace(acts, ws_id, root, None)
        assert emitted == []
