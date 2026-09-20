# Build a standalone MagCopy.exe. Needs: python -m pip install pyinstaller
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "$root\bin\ffmpeg.exe")) {
  Write-Host "bin\ffmpeg.exe is missing - run: python tools\fetch_binaries.py" -ForegroundColor Yellow
  exit 1
}

python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name MagCopy `
  --icon "$root\icon.ico" `
  --add-data "$root\fonts;fonts" `
  --add-data "$root\bin;bin" `
  --add-data "$root\icon.ico;." `
  --hidden-import PIL._tkinter_finder `
  "$root\magcopy.pyw"

Write-Host "`nBuilt dist\MagCopy.exe" -ForegroundColor Green
