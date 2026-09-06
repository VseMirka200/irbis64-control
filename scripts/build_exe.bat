@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title Build IRBIS64 Control EXE

set "PYTHON_CMD="
where py.exe >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto python_found
where python.exe >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto python_found

echo.
echo ERROR: Python 3 was not found.
echo Install Python 3.11 or newer and enable "Add Python to PATH".
echo.
pause
exit /b 1

:python_found
set "VENV_PY=.venv\Scripts\python.exe"
set "BOOTSTRAP_TMP=%CD%\.venv\.tmp"
if not exist "%BOOTSTRAP_TMP%" (
  mkdir "%BOOTSTRAP_TMP%"
  if errorlevel 1 goto error
)
set "TMP=%BOOTSTRAP_TMP%"
set "TEMP=%BOOTSTRAP_TMP%"

if exist "%VENV_PY%" (
  "%VENV_PY%" -m pip --version >nul 2>nul
  if not errorlevel 1 goto venv_ready
  echo Repairing incomplete virtual environment...
  "%VENV_PY%" -m ensurepip --upgrade --default-pip
  if errorlevel 1 goto error
  goto venv_ready
)

%PYTHON_CMD% -m venv ".venv"
if errorlevel 1 goto error

:venv_ready
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto error
"%VENV_PY%" -m pip install -r "requirements.txt"
if errorlevel 1 goto error
"%VENV_PY%" -m pip install "pyinstaller>=6.6,<7"
if errorlevel 1 goto error

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

rem Main application.
"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "IRBIS64Control" ^
  --icon "assets\irbis64_control.ico" ^
  --add-data "assets;assets" ^
  "main.py"
if errorlevel 1 goto error

rem Separate direct database connector. It stays next to the main EXE and is launched from the Tools button.
"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "IRBIS64ControlDB" ^
  --icon "assets\irbis64_control.ico" ^
  --add-data "assets;assets" ^
  "db_connector.py"
if errorlevel 1 goto error

"%VENV_PY%" -c "from pathlib import Path; p=Path('dist/IRBIS64Control.exe'); p.rename(Path('dist') / '\u0418\u0420\u0411\u0418\u042164 \u041a\u043e\u043d\u0442\u0440\u043e\u043b\u044c.exe')"
if errorlevel 1 goto error

echo.
echo Build completed. Keep BOTH files in dist together:
echo   ИРБИС64 Контроль.exe
echo   IRBIS64ControlDB.exe
echo.
pause
exit /b 0

:error
echo.
echo ERROR: EXE build failed.
echo Review the error message shown above.
echo.
pause
exit /b 1
