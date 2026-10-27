@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
if not defined PY (
    where py >nul 2>nul && set PY=py -3
)
if not defined PY (
    echo Python не найден. Установите с https://www.python.org/downloads/
    echo и отметьте галочку "Add python.exe to PATH".
    pause
    exit /b 1
)

%PY% -m pip install --quiet --upgrade pdf-sign-kit
if errorlevel 1 (
    echo Не удалось установить pdf-sign-kit. Проверьте интернет.
    pause
    exit /b 1
)

%PY% setup_by_text.py
pause >nul