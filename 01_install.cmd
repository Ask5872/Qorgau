@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || goto missing_files
echo Qorgau - установка системы экзамена
if not exist "requirements.txt" goto missing_files
if not exist "requirements-desktop.txt" goto missing_files
if not exist "run.py" goto missing_files
if not exist "scripts\check_setup.py" goto missing_files
if not exist "qorgau\server.py" goto missing_files
if not exist "web\index.html" goto missing_files
if exist ".venv\Scripts\python.exe" goto install
py -3.12 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto install
py -3.11 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto install
python -m venv .venv
if errorlevel 1 goto failure
:install
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-desktop.txt
if errorlevel 1 goto failure
".venv\Scripts\python.exe" scripts\check_setup.py --load-models
if errorlevel 1 goto failure
echo.
echo Установка завершена. Запустите 02_start.cmd для реального экзамена.
pause
exit /b 0
:missing_files
echo.
echo Не найдены файлы проекта рядом с установщиком.
echo Возможно, вы запустили этот файл прямо из ZIP-архива.
echo.
echo 1. Закройте это окно.
echo 2. Нажмите правой кнопкой по ZIP и выберите «Извлечь всё».
echo 3. Выберите обычную папку, например на Рабочем столе.
echo 4. Откройте распакованную папку Qorgau.
echo 5. Убедитесь, что рядом есть requirements.txt и run.py.
echo 6. Снова запустите 01_install.cmd из этой папки.
echo.
echo Переустанавливать Python для исправления этой ошибки не нужно.
pause
exit /b 1
:failure
echo.
echo Установка или проверка моделей не завершилась.
echo Причина указана выше. Скопируйте последние строки ошибки.
echo Инструкция по исправлению: README.md
pause
exit /b 1
