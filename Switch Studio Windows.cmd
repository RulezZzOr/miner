@echo off
setlocal
wsl.exe --exec true >nul 2>&1
if errorlevel 1 (
  echo Nejprve nainstalujte a nastavte WSL2: wsl --install
  echo Postup nastaveni Windows najdete v INSTALL.md.
  pause
  exit /b 1
)
wsl.exe --exec bash -s -- "%~dp0." %* < "%~dp0scripts\windows-wsl.sh"
set "studio_exit=%errorlevel%"
if not "%studio_exit%"=="0" pause
exit /b %studio_exit%
