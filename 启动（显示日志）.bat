@echo off
setlocal
cd /d "%~dp0"
title KeyHeat

set "PY="
python -c "print(1)" >nul 2>nul && set "PY=python"

if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"

if not defined PY (
  echo.
  echo  [!] Python 3.8+ not found.
  echo      Install from https://www.python.org/downloads/
  echo      ^(check "Add python.exe to PATH" during setup^)
  echo.
  pause
  exit /b 1
)

"%PY%" keyheat.py %*
if errorlevel 1 (
  echo.
  echo  [!] KeyHeat exited with an error.
  pause
)
endlocal
