@echo off
rem Objection! - quick start for Windows (double-click). Arguments are passed to objection.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
