@echo off
rem Copies MagCopy into your user profile, makes Start menu and desktop shortcuts, and starts it.
rem Nothing here needs administrator rights and nothing is written outside your own account.
setlocal EnableDelayedExpansion
title Install MagCopy
color 0B

set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\MagCopy"
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs\MagCopy.lnk"
set "DT=%USERPROFILE%\Desktop\MagCopy.lnk"

echo.
echo   MagCopy
echo   =======
echo.

rem Windows opens a .zip like a folder, but nothing inside it is really unpacked. Double-clicking
rem a .bat in that view copies only the .bat to a temp folder and runs it there - so the check
rem that catches it is simply whether the rest of the program is sitting next to this file. That
rem is a fact about the disk rather than a guess about the path, so it cannot be fooled.
if not exist "%SRC%MagCopy.exe" goto :not_extracted
if not exist "%SRC%_internal\" goto :not_extracted

echo   Installing to:
echo     %DEST%
echo.

"%SystemRoot%\System32\taskkill.exe" /IM MagCopy.exe /F >nul 2>&1

"%SystemRoot%\System32\robocopy.exe" "%SRC%." "%DEST%" /E /PURGE /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul
if errorlevel 8 (
  echo   The copy failed. Close anything using MagCopy and try again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($p in @('%SM%','%DT%')) { $s = $w.CreateShortcut($p); $s.TargetPath = '%DEST%\MagCopy.exe'; $s.WorkingDirectory = '%DEST%'; $s.IconLocation = '%DEST%\MagCopy.exe,0'; $s.Description = 'Screenshot and GIF shortcuts'; $s.Save() }" >nul 2>&1

echo   Done. Shortcuts added to the Start menu and the desktop.
echo.
echo   Ctrl+Shift+A   drag a rectangle - the screenshot goes straight to your clipboard
echo   Ctrl+Shift+S   drag a rectangle and record it - Escape stops and opens the editor
echo.
echo   Both shortcuts can be changed to anything you like in the MagCopy window,
echo   which lives in the notification area by the clock.
echo.
echo   To remove it later, run "Uninstall MagCopy.bat" from:
echo     %DEST%
echo.

start "" "%DEST%\MagCopy.exe"
echo   Started. You can close this window.
"%SystemRoot%\System32\timeout.exe" /t 8 >nul
exit /b 0

:not_extracted
echo   MagCopy.exe and the _internal folder are not next to this file, so there is
echo   nothing here to install yet. Almost always that means the zip has not been
echo   extracted - Windows shows a .zip like a folder, but the files inside are not
echo   really there.
echo.
echo   Right-click MagCopy-windows.zip, choose "Extract All...", open the MagCopy
echo   folder it makes, and run this again from inside it.
echo.
pause
exit /b 1
