@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ============================================================
rem  Build KeyHeat.exe from source.
rem  Run this AFTER changing any .py or ui.html, then upload the
rem  new KeyHeat.exe as a GitHub Release asset.
rem  It does NOT launch the app.
rem
rem  Version number comes from the highest git tag (v1.2.3 -> 1.2.3),
rem  written into _version.py and bundled into the exe.
rem ============================================================

set "PY="
python -c "print(1)" >nul 2>nul && set "PY=python"
if not defined PY for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY (
  echo [error] Python not found.
  pause
  exit /b 1
)

rem ---------- resolve version from git tag ----------
rem  note: reading git output through a temp file on purpose. Passing
rem  options like --abbrev=0 to "for /f" gets mangled by cmd.
rem  Read it with "for /f", not "set /p": git writes LF-only lines and
rem  set /p would swallow several tags into one value.
set "TMPF=%TEMP%\keyheat_tag.txt"
set "TAG="
set "VER="

git rev-parse --git-dir >nul 2>nul
if errorlevel 1 (
  echo [warn] not a git repo - using fallback VERSION from common.py
  goto :no_tag
)

del /q "%TMPF%" 2>nul
git tag --sort=-v:refname > "%TMPF%" 2>nul
if exist "%TMPF%" for /f "usebackq delims=" %%l in ("%TMPF%") do if not defined TAG set "TAG=%%l"
del /q "%TMPF%" 2>nul

if not defined TAG (
  echo [warn] no git tag yet - using fallback VERSION from common.py
  echo        create one first:  git tag v1.1.0
  goto :no_tag
)

set "VER=!TAG!"
if /i "!VER:~0,1!"=="v" set "VER=!VER:~1!"

echo !VER!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [warn] tag "!TAG!" is not semver like v1.2.3 - using fallback VERSION
  set "VER="
  goto :no_tag
)

rem warn when the newest tag is not the commit that is actually on disk
del /q "%TMPF%" 2>nul
git rev-parse HEAD > "%TMPF%" 2>nul
set "HEADH="
if exist "%TMPF%" for /f "usebackq delims=" %%l in ("%TMPF%") do if not defined HEADH set "HEADH=%%l"
del /q "%TMPF%" 2>nul
git rev-list -n 1 "!TAG!" > "%TMPF%" 2>nul
set "TAGH="
if exist "%TMPF%" for /f "usebackq delims=" %%l in ("%TMPF%") do if not defined TAGH set "TAGH=%%l"
del /q "%TMPF%" 2>nul
if /i not "!HEADH!"=="!TAGH!" echo [warn] HEAD is not at tag !TAG! - exe will report !VER! but contain newer code
git status --porcelain 2>nul | findstr /r "." >nul && echo [warn] working tree has uncommitted changes

echo [0/3] version = !VER!   (from tag !TAG!)
> "_version.py" echo VERSION = "!VER!"
goto :build

:no_tag
if exist "_version.py" del /q "_version.py"
echo [0/3] version = fallback in common.py

:build
rem ---------- build venv, outside the repo ----------
set "VENV=%~dp0..\.buildenv"
set "VPY=%VENV%\Scripts\python.exe"

if not exist "%VPY%" (
  echo [1/3] creating build venv ...
  "%PY%" -m venv "%VENV%"
  if errorlevel 1 (
    echo [error] venv creation failed.
    pause
    exit /b 1
  )
)

echo [2/3] installing pyinstaller ...
"%VPY%" -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
  echo [error] pip install failed.
  pause
  exit /b 1
)

echo [3/3] building KeyHeat.exe ...
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name KeyHeat --icon icon.ico --add-data "ui.html;." --add-data "icon.ico;." keyheat.py
if errorlevel 1 (
  echo [error] build failed.
  pause
  exit /b 1
)

copy /y "dist\KeyHeat.exe" "KeyHeat.exe" >nul
if errorlevel 1 (
  echo [error] cannot overwrite KeyHeat.exe - is it still running? Quit it and retry.
  echo         the fresh build is kept at dist\KeyHeat.exe
  pause
  exit /b 1
)
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q KeyHeat.spec 2>nul

echo.
for %%f in ("KeyHeat.exe") do echo Done: KeyHeat.exe  (%%~zf bytes)
echo Next: commit, push the tag, create a Release, upload KeyHeat.exe as the asset.
pause
endlocal
