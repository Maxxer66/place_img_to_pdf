@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "source" (
    mkdir "source"
    echo Папка source создана. Положите в неё PDF и запустите снова.
    pause
    exit /b 1
)
if not exist "config.json" (
    echo Сначала запустите setup.bat — нужно настроить привязки.
    pause
    exit /b 1
)

set PY=
where python >nul 2>nul && set PY=python
if not defined PY (
    where py >nul 2>nul && set PY=py -3
)
if not defined PY (
    echo Python не найден. Установите с https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -m pip install --quiet --upgrade pdf-sign-kit
if errorlevel 1 (
    echo Не удалось установить pdf-sign-kit.
    pause
    exit /b 1
)

%PY% batch_stamp.py
echo.
echo Готово. Результаты в папке done (суффикс -mod).
pause >nul