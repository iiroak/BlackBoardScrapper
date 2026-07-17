$ErrorActionPreference = "Stop"

# Downloads the signed-by-release Windows installer; no Python or Git required.
$url = "https://github.com/iiroak/BlackBoardScrapper/releases/latest/download/Campus-Archive-Setup.exe"
$target = Join-Path $env:TEMP "Campus-Archive-Setup.exe"

Write-Host "Descargando Campus Archive..."
Invoke-WebRequest -Uri $url -OutFile $target
Write-Host "Iniciando instalador..."
Start-Process -FilePath $target -Wait
Remove-Item $target -Force -ErrorAction SilentlyContinue
