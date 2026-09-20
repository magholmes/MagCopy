@echo off
rem Removes the installed copy, its shortcuts and its start-with-Windows entry.
rem Your saved GIFs and screenshots are never touched.
setlocal
title Uninstall MagCopy

set "DEST=%LOCALAPPDATA%\Programs\MagCopy"
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs\MagCopy.lnk"
set "DT=%USERPROFILE%\Desktop\MagCopy.lnk"

echo.
echo   This removes MagCopy from:
echo     %DEST%
echo.
"%SystemRoot%\System32\choice.exe" /C YN /M "   Remove it"
if errorlevel 2 exit /b 0

"%SystemRoot%\System32\taskkill.exe" /IM MagCopy.exe /F >nul 2>&1
"%SystemRoot%\System32\reg.exe" delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MagCopy /f >nul 2>&1
del "%SM%" >nul 2>&1
del "%DT%" >nul 2>&1

rem This script is sitting inside the folder it has to delete, so hand the last step to a helper
rem that runs after this window is gone and then removes itself.
set "H=%TEMP%\magcopy-remove.cmd"
> "%H%" echo @echo off
>>"%H%" echo "%%SystemRoot%%\System32\ping.exe" -n 3 127.0.0.1 ^>nul
>>"%H%" echo rmdir /s /q "%DEST%"
>>"%H%" echo del "%%~f0"
start "" /min "%H%"

echo.
echo   Removed. Your saved captures are still where they were.
"%SystemRoot%\System32\timeout.exe" /t 4 >nul
exit /b 0
