@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title IRBIS64 Control
set "PYTHONDONTWRITEBYTECODE=1"

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
echo Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:python_found
set "VENV_PY=.venv\Scripts\python.exe"

rem Reuse a complete virtual environment, but do not trust a half-created one.
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import PyQt6, openpyxl, xlrd, rapidfuzz" >nul 2>nul
  if not errorlevel 1 goto run_venv
)

rem If the selected system Python already has everything, start immediately.
%PYTHON_CMD% -c "import PyQt6, openpyxl, xlrd, rapidfuzz" >nul 2>nul
if not errorlevel 1 goto run_system

rem Some Windows installations deny ensurepip access to the global TEMP folder.
rem A private temporary folder next to the program avoids that failure.
set "BOOTSTRAP_TMP=%~dp0.venv\.tmp"
if not exist "%BOOTSTRAP_TMP%" (
  mkdir "%BOOTSTRAP_TMP%"
  if errorlevel 1 goto error
)
set "TMP=%BOOTSTRAP_TMP%"
set "TEMP=%BOOTSTRAP_TMP%"

if exist "%VENV_PY%" goto repair_venv

echo Creating virtual environment...
%PYTHON_CMD% -m venv ".venv"
if errorlevel 1 goto error
goto install_packages

:repair_venv
echo Repairing incomplete virtual environment...
"%VENV_PY%" -m ensurepip --upgrade --default-pip
if errorlevel 1 goto error

:install_packages
echo Checking required packages...
"%VENV_PY%" -c "import PyQt6, openpyxl, xlrd, rapidfuzz" >nul 2>nul
if not errorlevel 1 goto run_venv

echo Installing required packages...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto error
"%VENV_PY%" -m pip install -r "requirements.txt"
if errorlevel 1 goto error

:run_venv
echo Starting IRBIS64 Control...
"%VENV_PY%" "%~dp0main.py"
if errorlevel 1 goto error
exit /b 0

:run_system
echo Starting IRBIS64 Control with system Python...
%PYTHON_CMD% "%~dp0main.py"
if errorlevel 1 goto error
exit /b 0

:error
echo.
echo ERROR: IRBIS64 Control could not be started.
echo Review the error message shown above.
echo.
pause
exit /b 1
