@echo off
setlocal enableextensions

rem Переключаем кодовую страницу на Windows-1251 (для ANSI)
chcp 1251 >nul

rem Переходим в папку скрипта (работает и с UNC-путями)
pushd "%~dp0" >nul 2>nul
if errorlevel 1 (
    echo Не удалось перейти в папку скрипта: %~dp0
    pause
    exit /b 1
)

rem --- Проверка структуры проекта ---
if not exist "source\" (
    mkdir "source" >nul 2>nul
    if errorlevel 1 (
        echo Не удалось создать папку source.
        popd
        pause
        exit /b 1
    )
    echo Папка source создана. Положите в неё PDF и запустите снова.
    popd
    pause
    exit /b 1
)

set "HAS_PDF="
for %%F in ("source\*.pdf") do set "HAS_PDF=1"
if not defined HAS_PDF (
    echo В папке source нет PDF-файлов. Положите PDF и запустите снова.
    popd
    pause
    exit /b 1
)

if not exist "config.json" (
    echo Сначала запустите setup.bat — нужно настроить привязки.
    popd
    pause
    exit /b 1
)

if not exist "batch_stamp.py" (
    echo Не найден batch_stamp.py рядом с run.bat.
    popd
    pause
    exit /b 1
)

rem --- Поиск рабочего интерпретатора Python ---
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python --version >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo Python не найден или не запускается.
    echo Установите Python с https://www.python.org/downloads/ и повторите.
    popd
    pause
    exit /b 1
)

rem --- Установка/обновление pdf-sign-kit ---
%PY% -m pip install --quiet --disable-pip-version-check --upgrade pdf-sign-kit
if errorlevel 1 (
    echo Не удалось установить pdf-sign-kit.
    echo Проверьте интернет и права на установку пакетов Python.
    popd
    pause
    exit /b 1
)

rem --- Запуск обработки PDF ---
%PY% batch_stamp.py
if errorlevel 1 (
    echo.
    echo Ошибка при выполнении batch_stamp.py.
    popd
    pause
    exit /b 1
)

echo.
echo Готово. Результаты в папке done (суффикс -mod).
popd
pause
exit /b 0