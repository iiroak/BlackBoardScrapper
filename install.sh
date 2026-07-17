#!/usr/bin/env bash
set -euo pipefail

# Installs the latest Linux AppImage without requiring Python, Git, or WSL.
APP_NAME="Campus Archive"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
APP_PATH="${BIN_DIR}/blackboardscrapper"
UNINSTALLER="${BIN_DIR}/uninstall-blackboardscrapper"
APP_URL="https://github.com/iiroak/BlackBoardScrapper/releases/latest/download/Campus-Archive-x86_64.AppImage"

command -v curl >/dev/null 2>&1 || { printf 'Necesitas curl instalado.\n' >&2; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { printf 'Por ahora el AppImage requiere Linux x86_64.\n' >&2; exit 1; }
mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
curl -fL --progress-bar "$APP_URL" -o "$APP_PATH"
chmod +x "$APP_PATH"

cat > "$DESKTOP_DIR/campus-archive.desktop" <<EOF
[Desktop Entry]
Name=$APP_NAME
Comment=Blackboard course backup manager
Exec=$APP_PATH
Icon=utilities-terminal
Type=Application
Categories=Utility;Education;
Terminal=false
EOF

cat > "$UNINSTALLER" <<EOF
#!/usr/bin/env bash
rm -f "$APP_PATH" "$DESKTOP_DIR/campus-archive.desktop" "\$0"
printf 'Campus Archive desinstalado. Los backups no fueron eliminados.\\n'
EOF
chmod +x "$UNINSTALLER"

printf '\nInstalado. Ejecuta: blackboardscrapper\n'
printf 'También puedes abrirlo desde el menú de aplicaciones.\n'
printf 'Para desinstalar: uninstall-blackboardscrapper\n'
"$APP_PATH" >/dev/null 2>&1 &
