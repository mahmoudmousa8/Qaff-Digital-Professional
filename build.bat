@echo off
setlocal
chcp 65001 >nul
title Qaff Digital Professional Builder

echo.
echo ========================================
echo   Qaff Digital Professional Builder
echo ========================================
echo.

echo [1/3] Checking dependencies...
pip install pyinstaller Pillow customtkinter playwright rich --quiet
if errorlevel 1 goto :fail

echo.
echo [2/3] Building EXE...
echo.

set ICON_ARG=
if exist "assets\logo_white.ico" set ICON_ARG=--icon "assets\logo_white.ico"

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "Qaff Digital Professional" ^
  %ICON_ARG% ^
  --add-data "auto_delete_script.py;." ^
  --add-data "see_hours_script.py;." ^
  --add-data "auto_set_script.py;." ^
  --add-data "end_screen_script.py;." ^
  --add-data "publish_posts_script.py;." ^
  --add-data "assets/logo.png;assets" ^
  --add-data "assets/logo_white.ico;assets" ^
  --add-data "assets/icon_blue.ico;assets" ^
  --add-data "assets/logo_blue.png;assets" ^
  --hidden-import customtkinter ^
  --hidden-import PIL ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import playwright ^
  --hidden-import playwright.sync_api ^
  --hidden-import rich ^
  --hidden-import rich.console ^
  --collect-all customtkinter ^
  --collect-all playwright ^
  --noconfirm ^
  app.py

if errorlevel 1 goto :fail

if exist "dist\Qaff Digital Professional.exe" (
    echo.
    echo Build successful!
    echo EXE location: dist\Qaff Digital Professional.exe
    goto :end
)

:fail
echo.
echo Build FAILED. Check errors above.
exit /b 1

:end
echo.
if not "%CI%"=="true" if not "%GITHUB_ACTIONS%"=="true" pause
endlocal
