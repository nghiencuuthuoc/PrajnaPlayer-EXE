@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "VENV_DIR=.venv_build_win_single"
set "BUILD_DIR=build_single"
set "DIST_DIR=dist_single"
set "SPEC_DIR=spec_single"
set "REL_DIR=release_single"
set "ENTRY="
set "APP_NAME="
set "ICO_PATH="
set "PNG_PATH="
set "VLC_ARGS="
echo ================================================
echo PrajnaPlayer Windows Single EXE Only
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
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%SPEC_DIR%" rmdir /s /q "%SPEC_DIR%"
if not exist "%REL_DIR%" mkdir "%REL_DIR%"
set "PYI_CMD=pyinstaller --noconfirm --clean --windowed --onefile --distpath %DIST_DIR% --workpath %BUILD_DIR% --specpath %SPEC_DIR% --name %APP_NAME%"
if defined ICO_PATH if exist "%ICO_PATH%" set "PYI_CMD=!PYI_CMD! --icon "%ICO_PATH%" --add-data "%ICO_PATH%;.""
if defined PNG_PATH if exist "%PNG_PATH%" set "PYI_CMD=!PYI_CMD! --add-data "%PNG_PATH%;.""
if defined VLC_ARGS set "PYI_CMD=!PYI_CMD! !VLC_ARGS!"
set "PYI_CMD=!PYI_CMD! "%ENTRY%""
call !PYI_CMD!
if exist "%DIST_DIR%\%APP_NAME%.exe" copy /y "%DIST_DIR%\%APP_NAME%.exe" "%REL_DIR%\%APP_NAME%.exe" >nul
if exist "%REL_DIR%\%APP_NAME%.exe" (
    start "" "%REL_DIR%\%APP_NAME%.exe"
    timeout /t 5 /nobreak >nul
    taskkill /F /IM "%APP_NAME%.exe" >nul 2>nul
)
set /p DO_CLEAN=Delete build helper data and keep only release_single\%APP_NAME%.exe ? [y/N] : 
if /I "%DO_CLEAN%"=="Y" call :cleanup
if /I "%DO_CLEAN%"=="YES" call :cleanup
goto :eof
:cleanup
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%SPEC_DIR%" rmdir /s /q "%SPEC_DIR%"
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
