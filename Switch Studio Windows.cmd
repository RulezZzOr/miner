@echo off
setlocal
wsl.exe --exec true >nul 2>&1
if errorlevel 1 (
  echo Install and initialize WSL2 first: wsl --install
  echo See INSTALL.md for the Windows setup steps.
  pause
  exit /b 1
)
wsl.exe --exec bash -s -- "%~dp0." %* < "%~dp0scripts\windows-wsl.sh"
set "studio_exit=%errorlevel%"
if not "%studio_exit%"=="0" pause
exit /b %studio_exit%
