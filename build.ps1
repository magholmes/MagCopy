# Build MagCopy. Needs: python -m pip install pyinstaller
#
#   .uild.ps1              one file:  dist\MagCopy.exe
#   .uild.ps1 -Folder      a folder:  dist\MagCopy\  plus dist\MagCopy-windows.zip
#
# The folder build exists because of antivirus, not because of size. A one-file PyInstaller
# binary unpacks a Python runtime into a temp directory and executes it from there, which is
# structurally what a dropper does - so heuristic scanners flag it, and Chrome reports the
# resulting reputation block as "virus detected". The folder build never self-extracts, and a
# .zip is not a directly executable download, so neither trigger applies.
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
  # The installer and the read-me ship inside the folder, so whoever extracts the zip sees the
  # thing to double-click next to the thing they downloaded.
  Copy-Item "$root\packaging\*" "$root\dist\MagCopy\" -Force

  $zip = "$root\dist\MagCopy-windows.zip"
  Remove-Item $zip -EA SilentlyContinue
  # The folder itself, not its contents: extracting gives one tidy MagCopy\ folder rather than
  # spraying an exe, a _internal directory and three loose files into someone's Downloads.
  Compress-Archive -Path "$root\dist\MagCopy" -DestinationPath $zip -CompressionLevel Optimal
  Write-Host "`nBuilt dist\MagCopy\ and dist\MagCopy-windows.zip" -ForegroundColor Green
} else {
  Write-Host "`nBuilt dist\MagCopy.exe" -ForegroundColor Green
}
