@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Сначала запустите 01_install.cmd.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" run.py --demo
pause
