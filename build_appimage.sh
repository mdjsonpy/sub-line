#!/usr/bin/env bash
# build_appimage.sh — build sub-line (Sub-line text editor) as a portable AppImage.
#
# Steps:
#   1. build venv with PySide6 + PyInstaller (+ app deps)
#   2. PyInstaller --onedir bundle
#   3. AppDir with AppRun + .desktop file
#   4. linuxdeploy prepares the AppDir (deps, icon, desktop links)
#   5. appimagetool packages it (cached runtime => works offline)
#   6. -> <out>/Sub-line-x86_64.AppImage
set -euo pipefail
cd "$(dirname "$0")"

APP=texteditor
VENV=build-venv
DIST=dist
OUT="."

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# --- 0. prerequisites -----------------------------------------------------
command -v python3 >/dev/null || { echo "error: python3 not found" >&2; exit 1; }
command -v curl    >/dev/null || { echo "error: curl not found"   >&2; exit 1; }

# --- 1. build environment -------------------------------------------------
# Skip pip entirely when the deps are already installed (offline-safe):
# pip would otherwise try to reach PyPI for the `pip` self-upgrade and fail.
log "Build venv + dependencies ($VENV)"
python3 -m venv "$VENV"
if "$VENV/bin/python" -c "import PySide6, pygments, chardet, cryptography, PyInstaller" 2>/dev/null; then
    echo "dependencies already present in $VENV — skipping pip (offline-safe)"
else
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r requirements.txt pyinstaller
fi

# --- 2. PyInstaller onedir ------------------------------------------------
log "PyInstaller onedir bundle"
rm -rf "$DIST/$APP" build
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name "$APP" \
    run.py

# --- 3. AppDir ------------------------------------------------------------
log "AppDir (AppRun + .desktop)"
APPDIR="$DIST/$APP"
[ -d "$APPDIR" ] || { echo "error: PyInstaller output missing: $APPDIR" >&2; exit 1; }

# App icon: appimagetool requires the desktop Icon key to be a name WITHOUT
# extension (Icon Theme Spec), then looks for <name>.png in the AppDir.
cp -f icons/hicolor/512x512/apps/texteditor.png "$APPDIR/$APP.png"

cat > "$APPDIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Sub-line
GenericName=Text Editor
Comment=A Sublime-like text editor with encrypted workspaces
Exec=$APP
Icon=$APP
Terminal=false
Categories=Utility;TextEditor;
EOF

# AppRun: make the Qt libs resolvable. linuxdeploy keeps an existing AppRun.
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE${PATH:+:$PATH}"
export LD_LIBRARY_PATH="$HERE${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/texteditor" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# --- 4. linuxdeploy (AppDir prep) -----------------------------------------
log "linuxdeploy (downloaded once, cached)"
LINUXDEPLOY="./linuxdeploy-x86_64.AppImage"
if [ ! -x "$LINUXDEPLOY" ]; then
    curl -fsSL -o "$LINUXDEPLOY" \
        https://github.com/linuxdeploy/linuxdeploy/releases/latest/download/linuxdeploy-x86_64.AppImage
    chmod +x "$LINUXDEPLOY"
fi

log "Preparing AppDir (linuxdeploy)"
"$LINUXDEPLOY" --appdir "$APPDIR"

# --- 5. runtime (cached => offline-safe) ----------------------------------
# appimagetool needs the AppImage type2 runtime. We cache it so the build does
# not require the network. If it is missing we download it, and if that fails
# (offline) we carve it out of any existing AppImage on disk.
RUNTIME="./runtime-x86_64"
if [ ! -f "$RUNTIME" ]; then
    log "No cached runtime — trying download"
    curl -fsSL -o "$RUNTIME" \
        https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64 || true
fi
if [ ! -f "$RUNTIME" ]; then
    log "Download unavailable — extracting runtime from an existing AppImage"
    SRC="$(find . .. -maxdepth 3 -type f -name '*.AppImage' ! -name 'linuxdeploy-*' 2>/dev/null | head -n1 || true)"
    [ -n "$SRC" ] || { echo "error: no runtime and no AppImage to extract it from" >&2; exit 1; }
    python3 - "$SRC" "$RUNTIME" <<'PY'
import struct, sys
src, out = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
assert data[:4] == b"\x7fELF", "not an AppImage (no ELF runtime)"
off = data.find(b"hsqs")
while off != -1:
    try:
        ino   = struct.unpack_from("<I", data, off + 4)[0]
        bsize = struct.unpack_from("<I", data, off + 12)[0]
    except struct.error:
        break
    if ino < 1_000_000 and bsize in (131072, 262144, 524288, 1048576):
        with open(out, "wb") as f:
            f.write(data[:off])
        print(f"runtime = first {off} bytes (squashfs @ {off})")
        sys.exit(0)
    off = data.find(b"hsqs", off + 1)
sys.exit("error: could not locate squashfs superblock in " + src)
PY
fi
chmod +x "$RUNTIME"

# --- 6. package ------------------------------------------------------------
log "Packaging AppImage -> $OUT/"
# appimagetool ships inside the linuxdeploy bundle; extract it once.
rm -rf squashfs-root
"$LINUXDEPLOY" --appimage-extract >/dev/null 2>&1
AT="$(find . -maxdepth 8 -path '*linuxdeploy-plugin-appimage/usr/bin/appimagetool' 2>/dev/null | head -n1)"
[ -n "$AT" ] || { echo "error: appimagetool not found in linuxdeploy bundle" >&2; exit 1; }
mkdir -p "$OUT"
"$AT" --runtime-file "$RUNTIME" "$APPDIR"
rm -rf squashfs-root

# --- 7. done ---------------------------------------------------------------
# Output name derives from the desktop "Name" (Sub-line), so match case-insensitively.
ARTIFACT="$(find "$OUT" -maxdepth 1 -type f -iname "sub-line-*.AppImage" 2>/dev/null | head -n1 || true)"
if [ -n "$ARTIFACT" ]; then
    log "Done"
    ls -lh "$ARTIFACT"
else
    echo "error: no AppImage produced in $OUT/" >&2
    exit 1
fi
