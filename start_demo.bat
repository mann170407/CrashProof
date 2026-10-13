@echo off
rem Double-click: start the Windows + Linux (WSL2) Crash Lab backends and open the UI.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_demo.ps1"
if errorlevel 1 pause
