@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Сначала запустите 01_install.cmd.
 pause
 exit /b 1
)
echo Реальная камера в браузере. Системная блокировка клавиш Windows недоступна.
echo Для отдельного защищённого окна используйте 02_start.cmd.
".venv\Scripts\python.exe" run.py
pause
