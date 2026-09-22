# plan

1. [x] todos: file type (.todo), model/store, view (board columns, drag & drop between buckets), open from tree, main-window action, tests
2. [x] categories in the workspace list (new/rename/delete; collapse; move workspaces in/out; persist)
   - store v2: `layout` entries (workspace|category) + `collapsed` ids; v1 auto-migrates
   - MainWindow: `new_category` / `rename_category` (blank/duplicate rejected) / `delete_category` (closes members, files preserved)
   - `move_workspace_up/down` stop at category boundaries; `move_workspace_to_category` appends into the category block
   - collapse state persisted; category blocks move together when reordered
   - tests: tests/test_workspace_categories.py (22 tests); full suite 53 passed + offscreen smoke OK
