@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title Build IRBIS64 Control Installer

if not exist "installer\IRBIS64Control.iss" (
  echo.
  echo ERROR: Installer definition was not found:
  echo   installer\IRBIS64Control.iss
  echo Restore the installer directory and run this script again.
  echo.
  goto error_pause
)

call "scripts\build_exe.bat" --no-pause
if errorlevel 1 goto error

if not exist "dist\IRBIS64ControlDB.exe" goto missing_executables
".venv\Scripts\python.exe" -c "from pathlib import Path; raise SystemExit(0 if (Path('dist') / '\u0418\u0420\u0411\u0418\u042164 \u041a\u043e\u043d\u0442\u0440\u043e\u043b\u044c.exe').is_file() else 1)"
if errorlevel 1 goto missing_executables

set "ISCC_CMD="
where ISCC.exe >nul 2>nul
if not errorlevel 1 set "ISCC_CMD=ISCC.exe"
if defined ISCC_CMD goto iscc_found

if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if defined ISCC_CMD goto iscc_found
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC_CMD goto iscc_found
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC_CMD goto iscc_found

echo.
echo ERROR: Inno Setup 6 was not found.
echo Install it from https://jrsoftware.org/isdl.php and run this script again.
echo.
goto error_pause

:iscc_found
for /f "tokens=2 delims== " %%V in ('findstr /b /c:"version = " pyproject.toml') do set "APP_VERSION=%%~V"
if not defined APP_VERSION goto error

"%ISCC_CMD%" /DMyAppVersion=%APP_VERSION% "installer\IRBIS64Control.iss"
if errorlevel 1 goto error

echo.
echo Installer build completed:
echo   dist\IRBIS64Control-Setup-%APP_VERSION%.exe
echo.
if /I not "%~1"=="--no-pause" pause
exit /b 0

:missing_executables
echo.
echo ERROR: One or both application EXE files are missing from dist.
echo Run scripts\build_exe.bat and review its output.
echo.
goto error_pause

:error
echo.
echo ERROR: Installer build failed.
echo Review the error message shown above.
echo.
:error_pause
if /I not "%~1"=="--no-pause" pause
exit /b 1
