"""Tests for vault password dialogs: accept returns the entered values,
cancel returns None, and ChangePasswordDialog rejects a new/confirm
mismatch.

The dialogs are driven non-interactively: their ``__init__`` is patched to
pre-fill the fields and a zero-timer accepts/rejects them once the blocking
``exec()`` enters the event loop.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QTimer

from texteditor.password_dialog import PasswordDialog, ChangePasswordDialog

app = None


def qapp() -> QApplication:
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def _drive(monkeypatch, cls, fill, accept: bool = True) -> None:
    """Pre-fill a dialog's fields and schedule accept/reject for when its
    blocking exec() starts the event loop."""
    seen: list = []
    orig_init = cls.__init__

    def patched(self, *a, **kw):
        orig_init(self, *a, **kw)
        fill(self)
        seen.append(self)

    monkeypatch.setattr(cls, "__init__", patched)
    if accept:
        QTimer.singleShot(0, lambda: [d.accept() for d in seen])
    else:
        QTimer.singleShot(0, lambda: [d.reject() for d in seen])


class TestPasswordDialog:
    def test_accept_returns_password(self, monkeypatch):
        qapp()
        _drive(monkeypatch, PasswordDialog, lambda d: d.field.setText("secret"))
        assert PasswordDialog.get_password(None, "Unlock") == "secret"

    def test_cancel_returns_none(self, monkeypatch):
        qapp()
        _drive(monkeypatch, PasswordDialog, lambda d: d.field.setText("secret"),
               accept=False)
        assert PasswordDialog.get_password(None, "Unlock") is None


class TestChangePasswordDialog:
    def test_accept_returns_pair(self, monkeypatch):
        qapp()

        def fill(d):
            d.old.setText("oldpw")
            d.new.setText("newpw")
            d.confirm.setText("newpw")

        _drive(monkeypatch, ChangePasswordDialog, fill)
        assert ChangePasswordDialog.get(None, "Change") == ("oldpw", "newpw")

    def test_cancel_returns_none(self, monkeypatch):
        qapp()
        _drive(monkeypatch, ChangePasswordDialog, lambda d: d.old.setText("o"),
               accept=False)
        assert ChangePasswordDialog.get(None, "Change") is None

    def test_mismatch_returns_none(self, monkeypatch):
        qapp()

        def fill(d):
            d.old.setText("oldpw")
            d.new.setText("newpw")
            d.confirm.setText("different")

        _drive(monkeypatch, ChangePasswordDialog, fill)
        assert ChangePasswordDialog.get(None, "Change") is None

    def test_accept_override_rejects_mismatch_directly(self):
        qapp()
        dlg = ChangePasswordDialog("Change")
        dlg.old.setText("o")
        dlg.new.setText("n")
        dlg.confirm.setText("x")
        dlg.accept()
        assert dlg.result() == QDialog.Rejected
