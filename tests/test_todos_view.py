from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QMimeData
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QLineEdit, QComboBox, QLabel, QListWidget, QWidget

from texteditor.todos_view import TodoBucket, TodoColumn, TodosView, TaskFormDialog, TODO_MIME

app = None

def qapp():
    global app
    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)
    return app


def make_view() -> TodosView:
    qapp()
    v = TodosView()
    assert v.add_category("A")
    assert v.add_category("B")
    return v


def make_payload(cat: str, bucket: str, indices: list[int]) -> QMimeData:
    mime = QMimeData()
    mime.setData(TODO_MIME, json.dumps({"cat": cat, "bucket": bucket, "indices": indices}).encode())
    return mime


class TestDndLogic:
    def test_payload_roundtrip(self):
        mime = make_payload("A", "todo", [0, 1])
        data = TodoBucket.parse_payload(mime)
        assert data == {"cat": "A", "bucket": "todo", "indices": [0, 1]}

    def test_parse_payload_rejects_foreign_mime(self):
        mime = QMimeData()
        mime.setText("hello")
        assert TodoBucket.parse_payload(mime) is None

    def test_parse_payload_rejects_bad_json(self):
        mime = QMimeData()
        mime.setData(TODO_MIME, b"{not json")
        assert TodoBucket.parse_payload(mime) is None

    def test_drop_allowed_same_bucket(self):
        v = make_view()
        col = v._board.widget(0)
        bucket = col.list
        assert bucket.drop_allowed(make_payload("A", "todo", [0])) is False

    def test_drop_allowed_cross_category_rejected(self):
        v = make_view()
        col = v._board.widget(0)
        bucket = col.list
        assert bucket.drop_allowed(make_payload("B", "todo", [0])) is False

    def test_drop_allowed_different_bucket(self):
        v = make_view()
        assert v.add_task("A", "todo", "x")
        col = v._board.widget(1)
        bucket = col.list
        assert bucket.drop_allowed(make_payload("A", "todo", [0])) is True

    def test_handle_drop_moves_task(self):
        v = make_view()
        assert v.add_task("A", "todo", "x")
        col = v._board.widget(1)
        assert col.list.handle_drop(make_payload("A", "todo", [0])) is True
        assert len(v.todos().tasks("A", "todo")) == 0
        assert len(v.todos().tasks("A", "doing")) == 1
        assert v.todos().tasks("A", "doing")[0].text == "x"

    def test_handle_drop_rejects_wrong_category(self):
        v = make_view()
        assert v.add_task("A", "todo", "x")
        assert v.add_task("B", "todo", "y")
        col = v._board.widget(1)
        # bucket belongs to category A (currently selected), B payload must be refused
        assert col.list.category == "A"
        assert col.list.handle_drop(make_payload("B", "todo", [0])) is False
        assert len(v.todos().tasks("B", "doing")) == 0

    def test_make_payload_from_selection(self):
        v = make_view()
        assert v.add_task("A", "todo", "one")
        assert v.add_task("A", "todo", "two")
        col = v._board.widget(0)
        bucket = col.list
        assert bucket.count() == 2
        mime = bucket.mimeData([bucket.model().index(1, 0)])
        data = TodoBucket.parse_payload(mime)
        assert data["cat"] == "A"
        assert data["indices"] == [1]


class TestViewAPI:
    def test_add_and_delete_category(self):
        v = make_view()
        assert v.add_category("C")
        assert v.todos().categories() == ["A", "B", "C"]
        assert v.delete_category("B")
        assert v.todos().categories() == ["A", "C"]

    def test_rename_category_keeps_tasks(self):
        v = make_view()
        assert v.add_task("A", "todo", "x")
        assert v.rename_category("A", "A2")
        assert v.todos().categories() == ["A2", "B"]
        assert v.todos().tasks("A2", "todo")[0].text == "x"

    def test_move_tasks_between_categories(self):
        v = make_view()
        assert v.add_task("A", "todo", "x")
        assert v.move_tasks("A", "todo", [0], "B", "doing")
        assert len(v.todos().tasks("B", "doing")) == 1
        assert len(v.todos().tasks("A", "todo")) == 0

    def test_set_task_priority(self):
        v = make_view()
        assert v.add_task("A", "todo", "x", 1)
        assert v.set_task_priority("A", "todo", 0, 3)
        assert v.todos().tasks("A", "todo")[0].priority == 3

    def test_delete_tasks(self):
        v = make_view()
        v.add_task("A", "todo", "one")
        v.add_task("A", "todo", "two")
        assert v.delete_tasks("A", "todo", [0])
        assert len(v.todos().tasks("A", "todo")) == 1
        assert v.todos().tasks("A", "todo")[0].text == "two"

    def test_load_save_roundtrip(self, tmp_path):
        v = make_view()
        v.add_task("A", "todo", "x", 3)
        v.add_task("B", "doing", "y", 2)
        p = os.fspath(tmp_path / "t.json")
        assert v.save_to_file(p)
        v2 = TodosView()
        assert v2.load_from_file(p)
        assert v2.todos().tasks("A", "todo")[0].priority == 3
        assert v2.todos().tasks("B", "doing")[0].text == "y"

    def test_load_missing_file(self):
        v = make_view()
        assert v.load_from_file("/nonexistent/path/todos.json") is False

    def test_board_columns_exist(self):
        v = make_view()
        assert v._board.count() == 3
        headers = [v._board.widget(i).header.text() for i in range(3)]
        assert headers[0].startswith("Todo")
        assert headers[1].startswith("Doing")
        assert headers[2].startswith("Done")

    def test_task_widget_shows_text_and_color(self):
        v = make_view()
        assert v.add_task("A", "todo", "important", 3)
        col = v._board.widget(0)
        assert col.list.count() == 1
        item = col.list.item(0)
        assert item.text() == "important"
        assert item.data(Qt.UserRole) == 3

    def test_add_task_updates_column(self):
        v = make_view()
        assert v.add_task("A", "todo", "from api")
        col = v._board.widget(0)
        assert col.list.count() == 1
        assert col.list.item(0).text() == "from api"
        assert v.todos().tasks("A", "todo")[0].text == "from api"

    def test_context_menu_new_todo_on_task(self, monkeypatch):
        v = make_view()
        assert v.add_task("A", "todo", "x")

        def fake_exec(self):
            self.text_edit.setText("New Todo")
            return QDialog.Accepted

        monkeypatch.setattr(TaskFormDialog, "exec", fake_exec)
        bucket = v._board.widget(0).list
        menu = bucket.build_menu(bucket.rect().center())
        assert menu is not None
        new_act = menu.actions()[0]
        assert new_act.text() == "New Todo"
        new_act.trigger()
        tasks = v.todos().tasks("A", "todo")
        assert len(tasks) == 2
        assert tasks[1].text == "New Todo"

    def test_context_menu_on_empty_list_has_only_new_todo(self):
        v = make_view()
        bucket = v._board.widget(0).list
        menu = bucket.build_menu(bucket.rect().center())
        assert menu is not None
        actions = [a for a in menu.actions() if not a.isSeparator()]
        assert [a.text() for a in actions] == ["New Todo"]

    def test_context_menu_new_todo_respects_bucket(self, monkeypatch):
        v = make_view()

        def fake_exec(self):
            self.text_edit.setText("from menu")
            return QDialog.Accepted

        monkeypatch.setattr(TaskFormDialog, "exec", fake_exec)
        bucket = v._board.widget(2).list  # Done column
        menu = bucket.build_menu(bucket.rect().center())
        menu.actions()[0].trigger()
        assert len(v.todos().tasks("A", "done")) == 1
        assert len(v.todos().tasks("A", "todo")) == 0

    def test_changed_signal_fires(self):
        v = make_view()
        events = []
        v.changed.connect(lambda: events.append(None))
        v.add_task("A", "todo", "x")
        assert len(events) == 1
