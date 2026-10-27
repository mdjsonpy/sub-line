"""Smoke test: exercise the editor stack headlessly (offscreen).

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor

from texteditor.theme import Theme
from texteditor.store import Workspace, WorkspacesStore, LayoutEntry, StoreData
from texteditor.binary import classify_file
from texteditor.highlighter import lexer_for_path
from texteditor.editor_view import EditorPane
from texteditor.binary_stub import BinaryPane
from texteditor.image_view import ImagePane
from texteditor.workspace import WorkspaceView
from texteditor.find_bar import FindBar
from texteditor.main_window import MainWindow

app = QApplication.instance() or QApplication(sys.argv)
theme = Theme()
app.setStyleSheet(theme.stylesheet())

passed = []

def check(name, cond):
    passed.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)

# --- 1. store round-trip ---
tmpdir = tempfile.mkdtemp()
spath = os.path.join(tmpdir, "ws.json")
store = WorkspacesStore(spath)
ws = Workspace(id="abc", name="Test", root="/tmp", tabs=["/a.py", "/b.txt"], active=1)
store.save(StoreData([ws], [LayoutEntry("workspace", "abc")], []))
loaded = store.load()
check("store roundtrip count", len(loaded.workspaces) == 1)
check("store roundtrip fields",
      loaded.workspaces[0].name == "Test" and loaded.workspaces[0].tabs == ["/a.py", "/b.txt"]
      and loaded.workspaces[0].active == 1)
check("store roundtrip layout", len(loaded.layout) == 1 and loaded.layout[0].kind == "workspace")
check("store roundtrip collapsed", loaded.collapsed == [])

# --- 2. binary classification ---
d = tempfile.mkdtemp()
pyf = os.path.join(d, "x.py"); open(pyf, "w").write("print('hi')\n")
check("classify .py as text", classify_file(pyf) == "text")
# fake png (magic)
png = os.path.join(d, "p.png"); open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
check("classify png magic as image", classify_file(png) == "image")
# unknown binary with null byte
binf = os.path.join(d, "blob.xyz"); open(binf, "wb").write(b"\x00\x01\x02\x03" * 10)
check("classify null-byte blob as binary", classify_file(binf) == "binary")
# unknown extension but text content
txt = os.path.join(d, "data.unknown"); open(txt, "w").write("hello world\n")
check("classify unknown ext text as text", classify_file(txt) == "text")

# --- 3. lexer ---
lex = lexer_for_path(pyf, "def f(): pass")
check("lexer for .py is python", lex.name == "Python")
lex2 = lexer_for_path(os.path.join(d, "nope.xyz"), "abc")
check("lexer fallback is text", lex2.name in ("Text", "Text only"))

# --- 4. EditorPane open/save + highlight ---
pane = EditorPane(theme)
pane.loadFile(pyf)
check("pane title", pane.title == "x.py")
check("pane text loaded", pane.toPlainText() == "print('hi')\n")
check("pane not modified after load", not pane.isModified())
# apply highlighter synchronously
pane._highlighter._cached_rev = -1
pane._highlighter.highlightBlock(pane.toPlainText())
# find a non-default foreground color somewhere in block 0
# (QTextBlock.charFormatAt is not bound in this PySide6 build -> use cursor reads)
doc = pane.document()
block = doc.begin()
has_format = False
for i in range(len(block.text())):
    c = QTextCursor(doc)
    c.setPosition(i)
    col = c.charFormat().foreground().color().name().lower()
    if col not in ("", "#d4d4d4"):
        has_format = True
        break
check("highlight applied distinct color", has_format)

# save round-trip
out = os.path.join(tmpdir, "saved.py")
# simulate a real edit (select-all + type); setPlainText does not mark modified
c = pane.textCursor()
c.movePosition(QTextCursor.Start)
c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
c.insertText("print('changed')\n")
check("pane modified after edit", pane.isModified())
ok = pane.saveAs(out)
check("saveAs ok", ok)
check("saved content", open(out).read() == "print('changed')\n")
check("not modified after save", not pane.isModified())

# --- 5. WorkspaceView open + to_data ---
wsv = WorkspaceView(Workspace(id="w1", name="W", root=d, tabs=[], active=0), theme)
p1 = wsv.open_file(pyf)
p2 = wsv.new_untitled()
check("workspace has 2 tabs", wsv.tab_count() == 2)
data = wsv.to_data()
check("to_data tabs", len(data.tabs) == 1 and data.tabs[0] == os.path.abspath(pyf))

# --- 5b. tab close plumbing (X button + middle-click path) ---
check("tab bar closable (X button shown)", wsv.tabs.tabBar().tabsClosable())
# emitting the BAR signal is exactly what a real X / middle-click does;
# in this PySide6 build it must forward to the QTabWidget signal + slot
wsv.tabs.tabBar().tabCloseRequested.emit(1)  # untitled pane, unmodified -> no dialog
check("bar tabCloseRequested closes tab", wsv.tab_count() == 1)

# --- 5c-ctx. tab context menu: close to the right / left / others ---
from PySide6.QtCore import Qt as _CtxQt
bar = wsv.tabs.tabBar()
check("tab bar custom context menu policy", bar.contextMenuPolicy() == _CtxQt.CustomContextMenu)
check("tab bar exposes tabContextMenuRequested signal", hasattr(bar, "tabContextMenuRequested"))
check("workspace handles tab context menu", callable(getattr(wsv, "show_tab_context_menu", None)))

ctx = WorkspaceView(Workspace(id="wctx", name="W", root=d, tabs=[], active=0), theme)
for _ in range(3):
    ctx.new_untitled()  # tabs 0,1,2: unmodified -> no save dialog
check("ctx workspace starts with 3 tabs", ctx.tab_count() == 3)

ctx.close_tabs_to_right(ctx.tabs, 0)
check("close to right keeps only leftmost", ctx.tab_count() == 1 and ctx.tabs.currentIndex() == 0)

for _ in range(2):
    ctx.new_untitled()  # tabs 0,1,2 again
ctx.close_tabs_to_left(ctx.tabs, 2)
check("close to left keeps only rightmost", ctx.tab_count() == 1 and ctx.tabs.currentIndex() == 0)

for _ in range(2):
    ctx.new_untitled()  # tabs 0,1,2 again
ctx.close_others(ctx.tabs, 1)
check("close others keeps the clicked tab", ctx.tab_count() == 1 and ctx.tabs.currentIndex() == 0)

# no-ops at the edges must not touch the single remaining tab
ctx.close_tabs_to_right(ctx.tabs, 0)
ctx.close_tabs_to_left(ctx.tabs, 0)
ctx.close_others(ctx.tabs, 0)
check("edge no-ops leave the tab alone", ctx.tab_count() == 1)

# --- 5c-split. two-pane relocate model (move, no clone; max 2 groups) ---
# real files so to_data records them (untitled panes have no filepath)
f1 = os.path.join(d, "f1.txt"); open(f1, "w").write("one\n")
f2 = os.path.join(d, "f2.txt"); open(f2, "w").write("two\n")
f3 = os.path.join(d, "f3.txt"); open(f3, "w").write("three\n")

# move a tab to the right -> creates the single allowed split group (relocate, no clone)
sp = WorkspaceView(Workspace(id="wsplit", name="W", root=d, tabs=[], active=0), theme)
sp.open_file(f1)
sp.open_file(f2)                       # group0: [f1, f2], current = f2
check("split starts with 2 tabs / 1 group", sp.tab_count() == 2 and len(sp._groups) == 1)
sp.move_tab(sp.tabs, 1, "right")       # move f2 into a new right group
check("move-right creates a 2nd group", len(sp._groups) == 2)
check("left holds f1 only", sp.left_group().count() == 1
      and sp.left_group().widget(0).filepath == os.path.abspath(f1))
check("right holds f2 only (relocated, not cloned)", sp.right_group().count() == 1
      and sp.right_group().widget(0).filepath == os.path.abspath(f2))
check("no duplicate after move", sum(g.count() for g in sp._groups) == 2)
check("right group is active after move", sp.active_group() is sp.right_group())

# move it back left -> the emptied group auto-collapses to a single group
sp.move_tab(sp.right_group(), 0, "left")
check("move-left auto-collapses to 1 group", len(sp._groups) == 1)
check("both tabs back in the single group", sp.tab_count() == 2 and sp.tabs.count() == 2)

# open_in_side from a single group creates the right group
sp2 = WorkspaceView(Workspace(id="wsplit2", name="W", root=d, tabs=[], active=0), theme)
sp2.open_file(f1)
sp2.open_in_side(f2, "right")
check("open_in_side creates the right group", len(sp2._groups) == 2)
check("open_in_side right pane is f2", sp2.right_group().count() == 1
      and sp2.right_group().widget(0).filepath == os.path.abspath(f2))
# opening an already-open file in a side just focuses it (no new group, no clone)
sp2.open_in_side(f1, "right")
check("open_in_side on an open file keeps 2 tabs", sum(g.count() for g in sp2._groups) == 2)

# end_split merges every tab back into one group
sp3 = WorkspaceView(Workspace(id="wsplit3", name="W", root=d, tabs=[], active=0), theme)
sp3.open_file(f1)
sp3.open_in_side(f2, "right")
check("end_split starts from 2 groups", len(sp3._groups) == 2)
sp3.end_split()
check("end_split -> 1 group", len(sp3._groups) == 1)
check("end_split keeps both tabs", sp3.tab_count() == 2 and sp3.tabs.count() == 2)

# a 3rd group is never created: new opens reuse the right group
sp4 = WorkspaceView(Workspace(id="wsplit4", name="W", root=d, tabs=[], active=0), theme)
sp4.open_file(f1)
sp4.open_in_side(f2, "right")
sp4.open_in_side(f3, "right")          # reuses the existing right group
check("3rd open reuses the right group (no 3rd group)", len(sp4._groups) == 2)
check("right group now holds f2 + f3", sp4.right_group().count() == 2)

# to_data records the two-group layout
sp5 = WorkspaceView(Workspace(id="wsplit5", name="W", root=d, tabs=[], active=0), theme)
sp5.open_file(f1)
sp5.open_file(f2)
sp5.move_tab(sp5.tabs, 1, "right")     # group0: [f1], group1: [f2]
sd = sp5.to_data()
check("to_data has 2 split groups", len(sd.splits) == 2)
check("to_data group 0 = [f1]", sd.splits[0] == [os.path.abspath(f1)])
check("to_data group 1 = [f2]", sd.splits[1] == [os.path.abspath(f2)])

# restore a 2-group layout from data
sp6 = WorkspaceView(Workspace(id="wsplit6", name="W", root=d, tabs=[], active=0), theme)
sd3 = Workspace(
    id="wsplit6", name="W", root=d, active=0,
    tabs=[os.path.abspath(f1), os.path.abspath(f2)],
    splits=[[os.path.abspath(f1)], [os.path.abspath(f2)]],
    split_actives=[0, 0],
)
sp6.sync_from_data(sd3)
check("restore: 2 groups", len(sp6._groups) == 2)
check("restore: group sizes", sp6._groups[0].count() == 1 and sp6._groups[1].count() == 1)

# restore caps a stale 3-group layout at 2 (extra tabs merge into the right group)
sp7 = WorkspaceView(Workspace(id="wsplit7", name="W", root=d, tabs=[], active=0), theme)
sd4 = Workspace(
    id="wsplit7", name="W", root=d, active=0,
    splits=[[os.path.abspath(f1)], [os.path.abspath(f2)], [os.path.abspath(f3)]],
)
sp7.sync_from_data(sd4)
check("restore caps at 2 groups", len(sp7._groups) == 2)
check("restore keeps all tabs (merged right)", sum(g.count() for g in sp7._groups) == 3)

# --- 5c. image preview pane ---
from PySide6.QtGui import QPixmap as _QPixmap
from PySide6.QtCore import Qt as _Qt
valid_png = os.path.join(tmpdir, "valid.png")
_pm = _QPixmap(40, 30)
_pm.fill(_Qt.darkGreen)
check("generated png saved", _pm.save(valid_png))
check("classify valid png as image", classify_file(valid_png) == "image")

img_pane = ImagePane(valid_png)
check("image pane title", img_pane.title == "valid.png")
check("image pane not modified", img_pane.isModified() is False)
check("image pane save no-op", img_pane.save() is False)
check("image pane canvas rendered",
      img_pane.canvas._pixmap is not None and not img_pane.canvas._pixmap.isNull())
check("image pane not fallback", not img_pane.isFallback())
s0 = img_pane.canvas.scale()
img_pane.zoomIn()
check("zoomIn increases scale", img_pane.canvas.scale() > s0)
img_pane.resetZoom()
check("resetZoom restores scale", img_pane.canvas.scale() == 1.0)
img_pane.zoomOut()
check("zoomOut decreases scale", img_pane.canvas.scale() < 1.0)
img_pane.resetZoom()

# image navigation: set_path swaps filepath + canvas in place (no new tab)
other_png = os.path.join(tmpdir, "other.png")
aaa_png = os.path.join(tmpdir, "aaa.png")
_pm2 = _QPixmap(50, 40); _pm2.fill(_Qt.darkRed)
_pm3 = _QPixmap(50, 40); _pm3.fill(_Qt.darkBlue)
check("second png saved", _pm2.save(other_png))
check("third png saved", _pm3.save(aaa_png))
img_pane.set_path(other_png)
check("set_path swaps the displayed image",
      img_pane.filepath == os.path.abspath(other_png))
check("set_path reloads the pixmap", img_pane.canvas._pixmap is not None
      and img_pane.canvas._pixmap.width() == 50)

# image_neighbor: alphabetically adjacent image in the same folder, wraps around
from texteditor.image_view import image_neighbor
_n = image_neighbor(other_png, 1)
check("neighbor +1 of other = valid",
      _n is not None and os.path.basename(_n) == "valid.png")
_n = image_neighbor(other_png, -1)
check("neighbor -1 of other = aaa",
      _n is not None and os.path.basename(_n) == "aaa.png")
_n = image_neighbor(valid_png, 1)
check("neighbor wraps +1 from valid -> aaa",
      _n is not None and os.path.basename(_n) == "aaa.png")
_n = image_neighbor(aaa_png, -1)
check("neighbor wraps -1 from aaa -> valid",
      _n is not None and os.path.basename(_n) == "valid.png")

# navigateRequested: Left/Right arrows emit -1 / +1
from PySide6.QtGui import QKeyEvent as _QKE
from PySide6.QtCore import QEvent as _QEV
_nav = []
img_pane.navigateRequested.connect(lambda s: _nav.append(s))
img_pane.keyPressEvent(_QKE(_QEV.KeyPress, _Qt.Key_Left, _Qt.NoModifier))
img_pane.keyPressEvent(_QKE(_QEV.KeyPress, _Qt.Key_Right, _Qt.NoModifier))
check("Left arrow emits navigateRequested(-1)", -1 in _nav)
check("Right arrow emits navigateRequested(+1)", 1 in _nav)

# workspace routes image files to ImagePane (not the placeholder BinaryPane)
wsv2 = WorkspaceView(Workspace(id="wimg", name="W", root=d, tabs=[], active=0), theme)
pane_img = wsv2.open_file(valid_png)
check("workspace opens png as ImagePane", isinstance(pane_img, ImagePane))
check("workspace image tab is not BinaryPane", not isinstance(pane_img, BinaryPane))

# unsupported format (HEIC) still classifies as image but falls back to a badge
heic = os.path.join(d, "sample.heic")
with open(heic, "wb") as fh:
    fh.write(b"\xff\x00\x00\x01ftypheic" + b"0" * 32)
check("classify heic as image", classify_file(heic) == "image")
heic_pane = ImagePane(heic)
check("heic pane is fallback", heic_pane.isFallback())

# --- 5d. video / audio playback panes ---
from texteditor.binary import is_video, is_audio
check("is_video extension check", is_video("a.mp4") and not is_video("a.mp3"))
check("is_audio extension check", is_audio("a.mp3") and not is_audio("a.mp4"))

mp4_a = os.path.join(d, "a.mp4"); open(mp4_a, "wb").write(b"fake mp4")
mp4_b = os.path.join(d, "b.mp4"); open(mp4_b, "wb").write(b"fake mp4")
mp3_a = os.path.join(d, "a.mp3"); open(mp3_a, "wb").write(b"fake mp3")
mp3_b = os.path.join(d, "b.mp3"); open(mp3_b, "wb").write(b"fake mp3")

from texteditor.media import video_neighbor, audio_neighbor, media_neighbor
_n = video_neighbor(mp4_a, 1)
check("video neighbor +1 of a = b",
      _n is not None and os.path.basename(_n) == "b.mp4")
_n = video_neighbor(mp4_b, 1)
check("video neighbor wraps b -> a",
      _n is not None and os.path.basename(_n) == "a.mp4")
_n = audio_neighbor(mp3_b, -1)
check("audio neighbor -1 of b = a",
      _n is not None and os.path.basename(_n) == "a.mp3")
_n = media_neighbor(mp3_a, 1)
check("media neighbor dispatches by kind (skips images in folder)",
      _n is not None and os.path.basename(_n) == "b.mp3")

try:
    from texteditor.video_view import VideoPane
    from texteditor.audio_view import AudioPane

    vpane = VideoPane(mp4_a)
    check("video pane title", vpane.title == "a.mp4")
    check("video pane not modified", vpane.isModified() is False)
    check("video pane save no-op", vpane.save() is False)
    vpane.set_path(mp4_b)
    check("video pane set_path swaps the file",
          vpane.filepath == os.path.abspath(mp4_b))

    _nav = []
    vpane.navigateRequested.connect(lambda s: _nav.append(s))
    vpane.keyPressEvent(_QKE(_QEV.KeyPress, _Qt.Key_Right, _Qt.NoModifier))
    check("video Right arrow emits navigateRequested(+1)", 1 in _nav)

    apane = AudioPane(mp3_a)
    check("audio pane title", apane.title == "a.mp3")
    check("audio pane not modified", apane.isModified() is False)

    # workspace routes video/audio to the players (not the placeholder stub)
    wsv3 = WorkspaceView(Workspace(id="wmedia", name="W", root=d, tabs=[], active=0), theme)
    pane_v = wsv3.open_file(mp4_a)
    check("workspace opens mp4 as VideoPane", isinstance(pane_v, VideoPane))
    check("workspace video tab is not BinaryPane", not isinstance(pane_v, BinaryPane))
    pane_a = wsv3.open_file(mp3_a)
    check("workspace opens mp3 as AudioPane", isinstance(pane_a, AudioPane))

    # arrow navigation through the workspace (same wiring as images)
    wsv3._navigate_media(pane_v, 1)
    check("workspace navigates the video pane to its neighbor",
          pane_v.filepath == os.path.abspath(mp4_b))
    wsv3._navigate_media(pane_a, 1)
    check("workspace navigates the audio pane to its neighbor",
          pane_a.filepath == os.path.abspath(mp3_b))
except ImportError:
    print("SKIP  QtMultimedia unavailable - video/audio panes not tested")

# --- 6. MainWindow boots (no saved workspaces) ---
mstore = WorkspacesStore(os.path.join(tmpdir, "ws2.json"))
mw = MainWindow(theme, mstore)
check("mainwindow has 0 workspaces", len(mw._workspaces) == 0)
check("mainwindow current index -1", mw._current_index == -1)

# --- 6b. WorkspaceList (tree model) ---
from texteditor.workspace_list import WorkspaceList
wsl = WorkspaceList()
ws_a = Workspace(id="id_a", name="a", root=d, tabs=[], active=0)
ws_b = Workspace(id="id_b", name="b", root=d, tabs=[], active=0)
wsl.set_model(
    [LayoutEntry("workspace", "id_a"), LayoutEntry("workspace", "id_b")],
    {"id_a": ws_a, "id_b": ws_b}, set(), "id_a",
)
check("WorkspaceList has 2 items", wsl.tree.topLevelItemCount() == 2)
check("WorkspaceList active has checkmark", "✓" in wsl.tree.topLevelItem(0).text(0))
check("WorkspaceList activated signal exists", hasattr(wsl, "activated"))
check("WorkspaceList closeRequested signal exists", hasattr(wsl, "closeRequested"))
check("WorkspaceList newRequested signal exists", hasattr(wsl, "newRequested"))
check("WorkspaceList collapsed_categories exists", hasattr(wsl, "collapsed_categories"))
wsl.set_model(
    [LayoutEntry("category", "Work"), LayoutEntry("workspace", "id_a"),
     LayoutEntry("workspace", "id_b")],
    {"id_a": ws_a, "id_b": ws_b}, set(), "id_a",
)
check("WorkspaceList renders category header", wsl.tree.topLevelItem(0).text(0).startswith("Work"))

# --- 6b2. Copy Absolute Path (file tree + workspace list) ---
from PySide6.QtWidgets import QApplication as _QA
from texteditor.file_tree import FileTree
ft = FileTree()
_QA.clipboard().setText("")
check("FileTree copy path -> clipboard",
      ft.copy_path_to_clipboard(os.path.abspath(pyf)) and _QA.clipboard().text() == os.path.abspath(pyf))
_QA.clipboard().setText("")
wsl.copy_path_to_clipboard(d)
check("WorkspaceList copy path -> clipboard", _QA.clipboard().text() == d)
_QA.clipboard().setText("sentinel")
check("copy path empty is no-op",
      ft.copy_path_to_clipboard("") is False and _QA.clipboard().text() == "sentinel")
# empty-area fallback target is the (absolute) root, copied verbatim by the helper
ft.setRoot(d)
check("file tree root stored absolute (empty-area fallback)", ft._root_path == os.path.abspath(d))

# --- 6c. Switch by id (regression test) ---
d1 = tempfile.mkdtemp()
d2 = tempfile.mkdtemp()
f_a = os.path.join(d1, "a.txt"); open(f_a, "w").write("A\n")
f_b = os.path.join(d2, "b.txt"); open(f_b, "w").write("B\n")

sw_store_path = os.path.join(tmpdir, "switch_ws.json")
sw_store = WorkspacesStore(sw_store_path)
sw_store.save(StoreData(
    [Workspace(id="sw1", name="WS1", root=d1, tabs=[f_a], active=0),
     Workspace(id="sw2", name="WS2", root=d2, tabs=[f_b], active=0)],
    [LayoutEntry("workspace", "sw1"), LayoutEntry("workspace", "sw2")],
    []))
mw2 = MainWindow(theme, sw_store)
check("switch: 2 workspaces loaded", len(mw2._workspaces) == 2)
check("switch: starts at index 0", mw2._current_index == 0)
check("switch: tree root is ws[0].root", mw2._view.current_root() == d1)
mw2.activate_workspace("sw2")
check("switch: current index is 1", mw2._current_index == 1)
check("switch: tree root changed to ws[1].root", mw2._view.current_root() == d2)
check("switch: tab is ws[1].tab", mw2._view.tab_count() == 1 and mw2._view.active_pane().filepath == os.path.abspath(f_b))

# --- 6d. Close to empty ---
close_store_path = os.path.join(tmpdir, "close_ws.json")
close_store = WorkspacesStore(close_store_path)
close_store.save(StoreData(
    [Workspace(id="cl1", name="CL", root=d1, tabs=[], active=0)],
    [LayoutEntry("workspace", "cl1")],
    []))
mw3 = MainWindow(theme, close_store)
check("close: 1 workspace", len(mw3._workspaces) == 1)
mw3.close_workspace("cl1")
check("close: 0 workspaces after close", len(mw3._workspaces) == 0)
check("close: current index -1", mw3._current_index == -1)

# --- 6e. Categories (new / rename / move / delete) ---
import texteditor.main_window as mw_mod
cat_store_path = os.path.join(tmpdir, "cat_ws.json")
cat_store = WorkspacesStore(cat_store_path)
cat_store.save(StoreData(
    [Workspace(id="c1", name="C1", root=d1, tabs=[], active=0),
     Workspace(id="c2", name="C2", root=d2, tabs=[], active=0)],
    [LayoutEntry("workspace", "c1"), LayoutEntry("category", "Work"),
     LayoutEntry("workspace", "c2")],
    []))
mw4 = MainWindow(theme, cat_store)
check("cats: 2 workspaces, 1 category",
      len(mw4._workspaces) == 2 and mw4._category_names() == ["Work"])

# rename category
_orig_getText = mw_mod.QInputDialog.getText
mw_mod.QInputDialog.getText = staticmethod(lambda *a, **k: ("Work2", True))
mw4.rename_category("Work")
mw_mod.QInputDialog.getText = _orig_getText
check("cats: rename updates layout", mw4._category_names() == ["Work2"])

# move workspace into the category
_orig_getItem = mw_mod.QInputDialog.getItem
mw_mod.QInputDialog.getItem = staticmethod(lambda *a, **k: ("Work2", True))
mw4.move_workspace_to_category("c1")
mw_mod.QInputDialog.getItem = _orig_getItem
check("cats: c1 moved under Work2",
      [(e.kind, e.ref) for e in mw4._layout] ==
      [("category", "Work2"), ("workspace", "c2"), ("workspace", "c1")])

# move workspace down within the category
mw4.move_workspace_down("c1")
check("cats: c1/c2 swapped in layout",
      [e.ref for e in mw4._layout if e.kind == "workspace"] == ["c2", "c1"])

# new category
mw_mod.QInputDialog.getText = staticmethod(lambda *a, **k: ("NewCat", True))
mw4.new_category()
mw_mod.QInputDialog.getText = _orig_getText
check("cats: new category appended", mw4._category_names() == ["Work2", "NewCat"])

# delete a category -> closes its members, keeps files
_orig_question = mw_mod.QMessageBox.question
mw_mod.QMessageBox.question = staticmethod(lambda *a, **k: mw_mod.QMessageBox.Yes)
mw4.delete_category("Work2")
mw_mod.QMessageBox.question = _orig_question
check("cats: members closed on category delete", len(mw4._workspaces) == 0)
check("cats: only NewCat remains",
      [(e.kind, e.ref) for e in mw4._layout] == [("category", "NewCat")])
check("cats: current index -1 after deleting current", mw4._current_index == -1)
check("cats: files on disk preserved", os.path.exists(f_a) and os.path.exists(f_b))

# --- 7. find bar matching ---
fb = FindBar()
fb.setPane(pane)
fb.find_edit.setText("print")
app.processEvents()
sels = pane.extraSelections()
check("find highlights >=1 match", len(sels) >= 1)

print("\n%d/%d checks passed" % (sum(1 for _, c in passed if c), len(passed)))
sys.exit(0 if all(c for _, c in passed) else 1)
