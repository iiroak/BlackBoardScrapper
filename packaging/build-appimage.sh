#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPDIR="$ROOT/dist/CampusArchive.AppDir"
TOOL="${APPIMAGETOOL:-$ROOT/.tools/appimagetool}"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
cp "$ROOT/dist/BlackBoardScrapper" "$APPDIR/usr/bin/BlackBoardScrapper"
cp "$ROOT/static/bb.svg" "$APPDIR/campus-archive.svg"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/BlackBoardScrapper" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/campus-archive.desktop" <<'EOF'
[Desktop Entry]
Name=Campus Archive
Comment=Blackboard course backup manager
Exec=BlackBoardScrapper
Icon=campus-archive
Type=Application
Categories=Utility;Education;
Terminal=false
EOF
cp "$APPDIR/campus-archive.desktop" "$APPDIR/usr/share/applications/campus-archive.desktop"

if [ ! -x "$TOOL" ]; then
    mkdir -p "$(dirname "$TOOL")"
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi

ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$ROOT/dist/Campus-Archive-x86_64.AppImage"
