# Build MagCopy. Needs: python -m pip install pyinstaller
#
#   ./build.ps1            one file:    dist/MagCopy.exe
#   ./build.ps1 -Folder    the release: dist/MagCopy-Setup.exe, zipped as MagCopy-windows.zip
#
# The release is an installer wrapping a folder build, and that is about antivirus rather than
# size. A one-file PyInstaller binary unpacks a Python runtime into a temp directory and runs it
# from there, which is structurally what a dropper does - so heuristic scanners match the
# bootloader and Chrome reports the download as "virus detected". The installer's payload is an
# ordinary folder that never self-extracts, an Inno Setup stub is one of the most widely seen
# executables on Windows, and a .zip is not a directly executable download.
#
# The one-file build is still worth keeping for anyone who wants no installer at all; it is the
# one browsers are most likely to block.

param([switch]$Folder)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "$root\bin\ffmpeg.exe")) {
  Write-Host "bin\ffmpeg.exe is missing - run: python tools\fetch_binaries.py" -ForegroundColor Yellow
  exit 1
}

$mode = if ($Folder) { "--onedir" } else { "--onefile" }
python -m PyInstaller --noconfirm --clean $mode --windowed `
  --name MagCopy `
  --icon "$root\icon.ico" `
  --add-data "$root\fonts;fonts" `
  --add-data "$root\bin;bin" `
  --add-data "$root\icon.ico;." `
  --hidden-import PIL._tkinter_finder `
  "$root\magcopy.pyw"

if ($Folder) {
  # One file in the download, and opening it installs the program. The folder PyInstaller just
  # built is the installer's payload, not the thing anyone sees.
  $iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) {
    Write-Host "Inno Setup is missing - run: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
    exit 1
  }

  # One version number, and it lives in the source rather than in this script.
  $ver = (Select-String -Path "$root\magcopy\settings.py" -Pattern '^APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
  Write-Host "`nBuilding the installer for $ver" -ForegroundColor Cyan

  Remove-Item "$root\dist\MagCopy-Setup.exe" -EA SilentlyContinue
  & $iscc /Q "/DMyVersion=$ver" "$root\packaging\magcopy.iss"
  if ($LASTEXITCODE -ne 0) { Write-Host "the installer did not compile" -ForegroundColor Red; exit 1 }

  $zip = "$root\dist\MagCopy-windows.zip"
  Remove-Item $zip -EA SilentlyContinue
  # Just the one file. Opening the zip should present a single thing to double-click, with no
  # folder to go into, nothing to read first and no way to pick the wrong one.
  Compress-Archive -Path "$root\dist\MagCopy-Setup.exe" -DestinationPath $zip -CompressionLevel Optimal

  $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
  Write-Host "`nBuilt dist\MagCopy-Setup.exe and dist\MagCopy-windows.zip ($mb MB)" -ForegroundColor Green
} else {
  Write-Host "`nBuilt dist\MagCopy.exe" -ForegroundColor Green
}
