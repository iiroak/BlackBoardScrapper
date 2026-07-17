#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}==>${NC} $*"; }
warn() { echo -e "${CYAN}-->${NC} $*"; }
err()  { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

# ── python3 ──────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        v=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || echo "(0,0)")
        major=$(echo "$v" | cut -d, -f1 | tr -dc 0-9)
        minor=$(echo "$v" | cut -d, -f2 | tr -dc 0-9)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"; break
        fi
    fi
done
[ -z "$PYTHON" ] && err "Necesitas Python 3.10+. Instalalo y volve a correr este script."

log "Python: $($PYTHON --version)"

# ── repo ─────────────────────────────────────────────────────────────
REPO="https://github.com/iiroak/BlackBoardScrapper.git"
APP_DIR="${HOME}/.blackboard-scraper"

if [ -d "$APP_DIR/.git" ]; then
    log "Repositorio existente, actualizando..."
    git -C "$APP_DIR" pull --ff-only
else
    log "Clonando $REPO ..."
    git clone "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"

# ── venv ─────────────────────────────────────────────────────────────
if [ ! -d venv ]; then
    log "Creando entorno virtual..."
    "$PYTHON" -m venv venv
fi
source venv/bin/activate

# ── pip ──────────────────────────────────────────────────────────────
log "Instalando dependencias..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── playwright ───────────────────────────────────────────────────────
if ! python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    warn "Playwright no detectado, reinstalando..."
    pip install playwright -q
fi
log "Instalando navegadores de Playwright..."
python -m playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium

# ── launcher ─────────────────────────────────────────────────────────
LAUNCHER="$BIN_DIR/campus-archive"
cat > "$LAUNCHER" <<'LAUNCHEREOF'
#!/usr/bin/env bash
APP_DIR="${HOME}/.blackboard-scraper"
source "${APP_DIR}/venv/bin/activate"
python "${APP_DIR}/run.py"
LAUNCHEREOF
chmod +x "$LAUNCHER"

# ── done ─────────────────────────────────────────────────────────────
echo ""
log "Instalacion completa!"
echo ""
echo -e "  ${CYAN}campus-archive${NC}    abri el panel web"
echo -e "  ${CYAN}python ${APP_DIR}/main.py${NC}   CLI (descarga todo)"
echo ""
log "Abriendo ahora..."
"$LAUNCHER"
