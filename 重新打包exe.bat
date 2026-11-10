@echo off
setlocal
cd /d "%~dp0"

set "PY="
python -c "print(1)" >nul 2>nul && set "PY=python"
if not defined PY for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY (
  echo [!] Python not found.
  pause
  exit /b 1
)

echo [1/2] installing pyinstaller ...
"%PY%" -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
  echo [!] pip install failed.
  pause
  exit /b 1
)

echo [2/2] building KeyHeat.exe ...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name KeyHeat --icon icon.ico --add-data "ui.html;." keyheat.py
if errorlevel 1 (
  echo [!] build failed.
  pause
  exit /b 1
)

copy /y "dist\KeyHeat.exe" "KeyHeat.exe" >nul
rmdir /s /q build 2>nul
del /q KeyHeat.spec 2>nul
rmdir /s /q dist 2>nul

echo.
echo Done: KeyHeat.exe
pause
endlocal
