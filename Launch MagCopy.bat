@echo off
set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0magcopy.pyw"
