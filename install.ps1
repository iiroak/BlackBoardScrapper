#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$Green = "`e[0;32m"; $Cyan = "`e[0;36m"; $Red = "`e[0;31m"; $NC = "`e[0m"
function log  { Write-Host "$Green==>$NC $args" -NoNewline; Write-Host "" }
function warn { Write-Host "$Cyan-->$NC $args" -NoNewline; Write-Host "" }
function err  { Write-Host "$Red ERROR:$NC $args" -ForegroundColor Red; exit 1 }

# ── admin check (solo avisa, no forza) ─────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { warn "No sos admin: si Playwright pide permisos para chromium, cerra y volve a correr como admin." }

# ── python ──────────────────────────────────────────────────────────
$python = $null
foreach ($cmd in @("python3","python")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $v = & $cmd -c "import sys; print(sys.version_info[:2])" 2>$null
        if ($v -match "\((\d+),\s*(\d+)\)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) { $python = $cmd; break }
        }
    }
}
if (-not $python) { err "Necesitas Python 3.10+. Bajalo de https://python.org y volve a correr." }

log "Python: $(& $python --version)"

# ── repo ────────────────────────────────────────────────────────────
$Repo  = "https://github.com/iiroak/BlackBoardScrapper.git"
$AppDir = Join-Path $env:USERPROFILE ".blackboard-scraper"

if (Test-Path (Join-Path $AppDir ".git")) {
    log "Repositorio existente, actualizando..."
    git -C $AppDir pull --ff-only
} else {
    log "Clonando $Repo ..."
    git clone $Repo $AppDir
}

Push-Location $AppDir

# ── venv ────────────────────────────────────────────────────────────
if (-not (Test-Path venv)) {
    log "Creando entorno virtual..."
    & $python -m venv venv
}
$VenvActivate = Join-Path $AppDir "venv\Scripts\Activate.ps1"
. $VenvActivate

# ── pip ─────────────────────────────────────────────────────────────
log "Instalando dependencias..."
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── playwright ──────────────────────────────────────────────────────
try { python -c "from playwright.sync_api import sync_playwright" } catch {
    warn "Playwright no detectado, reinstalando..."
    pip install playwright -q
}
log "Instalando navegadores de Playwright..."
python -m playwright install chromium

# ── launcher ────────────────────────────────────────────────────────
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = Join-Path $Desktop "Campus Archive.bat"
$BatContent = @"
@echo off
cd /d "$AppDir"
call "$(Join-Path $AppDir "venv\Scripts\activate.bat")"
python run.py
pause
"@
[System.IO.File]::WriteAllLines($Shortcut, $BatContent -split "`n")

# ── done ────────────────────────────────────────────────────────────
Write-Host ""
log "Instalacion completa!"
Write-Host ""
Write-Host "  En tu escritorio creamos: Campus Archive.bat (doble click)"
Write-Host "  Tambien podes: python $AppDir\main.py   (linea de comandos)"
Write-Host ""
log "Abriendo ahora..."
Pop-Location
Start-Process -FilePath $Shortcut
