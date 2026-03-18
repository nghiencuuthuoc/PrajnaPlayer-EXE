@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "VENV_DIR=.venv_build_win"
set "ENTRY="
set "APP_NAME="
set "ICO_PATH="
set "PNG_PATH="
set "VLC_ARGS="
echo ================================================
echo PrajnaPlayer Windows Build Kit
echo ================================================
echo 1. PrajnaPlayer_v19_dualsub_color_speed.py
echo 2. PrajnaPlayer_Dual_Subtitle_v3_state_resume.py
echo.
set /p CHOICE=Choose app [1/2] : 
if "%CHOICE%"=="2" (
    set "ENTRY=PrajnaPlayer_Dual_Subtitle_v3_state_resume.py"
    set "APP_NAME=PrajnaPlayer_Dual_Subtitle_v3"
) else (
    set "ENTRY=PrajnaPlayer_v19_dualsub_color_speed.py"
    set "APP_NAME=PrajnaPlayer_v19"
)
call :find_icon_ico
call :find_icon_png
call :detect_vlc
where py >nul 2>nul
if %errorlevel%==0 (
    py -m venv "%VENV_DIR%"
) else (
    python -m venv "%VENV_DIR%"
)
call "%VENV_DIR%\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
set "PYI_CMD=pyinstaller --noconfirm --clean --windowed --onefile --name %APP_NAME%"
if defined ICO_PATH if exist "%ICO_PATH%" set "PYI_CMD=!PYI_CMD! --icon "%ICO_PATH%" --add-data "%ICO_PATH%;.""
if defined PNG_PATH if exist "%PNG_PATH%" set "PYI_CMD=!PYI_CMD! --add-data "%PNG_PATH%;.""
if defined VLC_ARGS set "PYI_CMD=!PYI_CMD! !VLC_ARGS!"
set "PYI_CMD=!PYI_CMD! "%ENTRY%""
call !PYI_CMD!
if exist "dist\%APP_NAME%.exe" (
    start "" "dist\%APP_NAME%.exe"
    timeout /t 5 /nobreak >nul
    taskkill /F /IM "%APP_NAME%.exe" >nul 2>nul
)
set /p DO_CLEAN=Delete build helper data (venv, build, __pycache__, spec)? [y/N] : 
if /I "%DO_CLEAN%"=="Y" call :cleanup
if /I "%DO_CLEAN%"=="YES" call :cleanup
goto :eof
:cleanup
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
if exist "build" rmdir /s /q "build"
for %%F in (*.spec) do del /q "%%F"
for /d /r %%D in (__pycache__) do if exist "%%D" rmdir /s /q "%%D"
exit /b 0
:find_icon_ico
set "ICO_PATH="
for %%P in ("..\nct_logo.ico" ".\nct_logo.ico" "..\..\nct_logo.ico" "..\..\..\nct_logo.ico" "..\..\..\..\nct_logo.ico") do (
    if not defined ICO_PATH if exist %%~P set "ICO_PATH=%%~fP"
)
exit /b 0
:find_icon_png
set "PNG_PATH="
for %%P in ("..\nct_logo.png" ".\nct_logo.png" "..\..\nct_logo.png" "..\..\..\nct_logo.png" "..\..\..\..\nct_logo.png") do (
    if not defined PNG_PATH if exist %%~P set "PNG_PATH=%%~fP"
)
exit /b 0
:detect_vlc
set "VLC_ARGS="
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"
if defined VLC_DIR (
    set "VLC_ARGS=--add-binary "%VLC_DIR%\libvlc.dll;." --add-binary "%VLC_DIR%\libvlccore.dll;." --add-data "%VLC_DIR%\plugins;plugins""
)
exit /b 0
