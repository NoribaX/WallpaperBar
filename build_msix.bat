@echo off
setlocal EnableExtensions DisableDelayedExpansion

pushd "%~dp0"
if errorlevel 1 goto :fatal_no_pushd

set "SCRIPT_DIR=%CD%"
set "SOURCE_DIR="
set "STAGE_DIR=%SCRIPT_DIR%\package_staging"
set "DIST_DIR=%SCRIPT_DIR%\dist"
set "BUILD_DIR=%SCRIPT_DIR%\build\pyinstaller"
set "APP_NAME=WallpaperBar"
set "OUTPUT_MSIX=%SCRIPT_DIR%\%APP_NAME%.msix"
set "TEMP_MSIX=%SCRIPT_DIR%\%APP_NAME%.building.msix"
set "PYTHON_EXE="
set "PYTHON_ARGS="
set "MAKEAPPX="
set "SDK_ROOT="
set "VIDEO_DLL="

echo.
echo WallpaperBar Pro MSIX release build
echo Packaging directory: "%SCRIPT_DIR%"

if defined WBP_SOURCE_DIR set "SOURCE_DIR=%WBP_SOURCE_DIR%"
if not defined SOURCE_DIR for %%I in ("%SCRIPT_DIR%\..\WallpaperBar Pro") do set "SOURCE_DIR=%%~fI"
if not exist "%SOURCE_DIR%\main.py" (
    echo ERROR: main.py was not found in source directory "%SOURCE_DIR%".
    goto :fail
)
for %%I in ("%SOURCE_DIR%") do set "SOURCE_DIR=%%~fI"

for %%F in (main.py wallpaper_video.py style.css app_icon.ico) do (
    if not exist "%SOURCE_DIR%\%%F" (
        echo ERROR: Required source file is missing: "%SOURCE_DIR%\%%F"
        goto :fail
    )
)
for %%F in (AppxManifest.xml Assets\StoreLogo.png Assets\Square150x150Logo.png Assets\Square44x44Logo.png) do (
    if not exist "%SCRIPT_DIR%\%%F" (
        echo ERROR: Required packaging file is missing: "%SCRIPT_DIR%\%%F"
        goto :fail
    )
)

rem Select Python from an explicit override, project venv, PATH, or the Python launcher.
if defined WBP_PYTHON (
    set "PYTHON_EXE=%WBP_PYTHON%"
    call :check_python
    if errorlevel 1 goto :python_override_failed
    goto :python_ready
)
if exist "%SOURCE_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%SOURCE_DIR%\.venv\Scripts\python.exe"
    call :check_python
    if not errorlevel 1 goto :python_ready
    set "PYTHON_EXE="
)
for %%I in (python.exe) do if not defined PYTHON_EXE if exist "%%~$PATH:I" set "PYTHON_EXE=%%~$PATH:I"
if defined PYTHON_EXE (
    call :check_python
    if not errorlevel 1 goto :python_ready
    set "PYTHON_EXE="
)
for %%I in (py.exe) do if not defined PYTHON_EXE if exist "%%~$PATH:I" set "PYTHON_EXE=%%~$PATH:I"
if defined PYTHON_EXE (
    set "PYTHON_ARGS=-3"
    call :check_python
    if not errorlevel 1 goto :python_ready
)
echo ERROR: No Python interpreter with PyInstaller, PySide6, and keyboard was found.
echo Install those packages in a Python 3 environment or set WBP_PYTHON to python.exe.
goto :fail

:python_ready

rem Resolve the Windows SDK packaging tool from PATH, SDK environment variables, or standard SDK folders.
if defined WBP_MAKEAPPX set "MAKEAPPX=%WBP_MAKEAPPX%"
if not defined MAKEAPPX for %%I in (makeappx.exe) do if exist "%%~$PATH:I" set "MAKEAPPX=%%~$PATH:I"
if defined WindowsSdkDir set "SDK_ROOT=%WindowsSdkDir%"
if not defined SDK_ROOT for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows Kits\Installed Roots" /v KitsRoot10 2^>nul') do if /i "%%A"=="REG_SZ" set "SDK_ROOT=%%B"
if not defined MAKEAPPX if defined SDK_ROOT if defined WindowsSDKVersion if exist "%SDK_ROOT%bin\%WindowsSDKVersion%x64\makeappx.exe" set "MAKEAPPX=%SDK_ROOT%bin\%WindowsSDKVersion%x64\makeappx.exe"
if not defined MAKEAPPX if defined SDK_ROOT if exist "%SDK_ROOT%bin" (
    for /f "delims=" %%V in ('dir /b /ad /o-n "%SDK_ROOT%bin" 2^>nul') do if not defined MAKEAPPX if exist "%SDK_ROOT%bin\%%V\x64\makeappx.exe" set "MAKEAPPX=%SDK_ROOT%bin\%%V\x64\makeappx.exe"
)
if not defined MAKEAPPX if exist "%ProgramFiles(x86)%\Windows Kits\10\bin" (
    for /f "delims=" %%V in ('dir /b /ad /o-n "%ProgramFiles(x86)%\Windows Kits\10\bin" 2^>nul') do if not defined MAKEAPPX if exist "%ProgramFiles(x86)%\Windows Kits\10\bin\%%V\x64\makeappx.exe" set "MAKEAPPX=%ProgramFiles(x86)%\Windows Kits\10\bin\%%V\x64\makeappx.exe"
)
if not defined MAKEAPPX if exist "%ProgramFiles%\Windows Kits\10\bin" (
    for /f "delims=" %%V in ('dir /b /ad /o-n "%ProgramFiles%\Windows Kits\10\bin" 2^>nul') do if not defined MAKEAPPX if exist "%ProgramFiles%\Windows Kits\10\bin\%%V\x64\makeappx.exe" set "MAKEAPPX=%ProgramFiles%\Windows Kits\10\bin\%%V\x64\makeappx.exe"
)
if not defined MAKEAPPX (
    echo ERROR: makeappx.exe was not found.
    echo Install the Windows 10/11 SDK or set WBP_MAKEAPPX to its full path.
    goto :fail
)
if not exist "%MAKEAPPX%" (
    echo ERROR: The configured makeappx.exe does not exist: "%MAKEAPPX%"
    goto :fail
)

rem Prefer the same newest engine variant that wallpaper_video.py loads first.
if defined WBP_VIDEO_DLL set "VIDEO_DLL=%WBP_VIDEO_DLL%"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-raised-layer5\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-raised-layer5\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-raised-layer4\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-raised-layer4\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-raised-layer3\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-raised-layer3\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-raised-layer2\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-raised-layer2\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-desktop-layer\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-desktop-layer\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build-hotfix\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build-hotfix\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\build\Release\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\build\Release\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL if exist "%SOURCE_DIR%\native\WallpaperVideoEngine.dll" set "VIDEO_DLL=%SOURCE_DIR%\native\WallpaperVideoEngine.dll"
if not defined VIDEO_DLL (
    echo ERROR: WallpaperVideoEngine.dll was not found under "%SOURCE_DIR%\native".
    echo Build the x64 native engine first or set WBP_VIDEO_DLL to the final DLL.
    goto :fail
)
if not exist "%VIDEO_DLL%" (
    echo ERROR: The configured video engine DLL does not exist: "%VIDEO_DLL%"
    goto :fail
)
for %%I in ("%VIDEO_DLL%") do if %%~zI LEQ 0 (
    echo ERROR: The video engine DLL is empty: "%VIDEO_DLL%"
    goto :fail
)
call :check_x64_pe "%VIDEO_DLL%"
if errorlevel 1 (
    echo ERROR: WallpaperVideoEngine.dll is not a valid x64 Windows DLL: "%VIDEO_DLL%"
    goto :fail
)

echo.
echo [1/4] Building the application with "%PYTHON_EXE%"...
if exist "%DIST_DIR%\%APP_NAME%" (
    rmdir /s /q "%DIST_DIR%\%APP_NAME%"
    if exist "%DIST_DIR%\%APP_NAME%" (
        echo ERROR: Could not clear stale PyInstaller output "%DIST_DIR%\%APP_NAME%".
        goto :fail
    )
)
"%PYTHON_EXE%" %PYTHON_ARGS% -m PyInstaller --noconfirm --clean --name "%APP_NAME%" --windowed --onedir ^
    --icon "%SOURCE_DIR%\app_icon.ico" ^
    --distpath "%DIST_DIR%" --workpath "%BUILD_DIR%" --specpath "%BUILD_DIR%" ^
    --add-data "%SOURCE_DIR%\style.css;." ^
    --add-data "%SOURCE_DIR%\app_icon.ico;." ^
    --hidden-import "keyboard._winkeyboard" ^
    --hidden-import "wallpaper_video" ^
    "%SOURCE_DIR%\main.py"
if errorlevel 1 (
    echo ERROR: PyInstaller failed. Review its output above.
    goto :fail
)
if not exist "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe" (
    echo ERROR: PyInstaller returned success but "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe" is missing.
    goto :fail
)
call :check_x64_pe "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe"
if errorlevel 1 (
    echo ERROR: PyInstaller output is not a valid x64 Windows executable.
    goto :fail
)
echo.
echo [2/4] Preparing a clean staging directory...
if exist "%STAGE_DIR%" (
    rmdir /s /q "%STAGE_DIR%"
    if exist "%STAGE_DIR%" (
        echo ERROR: Could not clear staging directory "%STAGE_DIR%".
        goto :fail
    )
)
mkdir "%STAGE_DIR%"
if errorlevel 1 (
    echo ERROR: Could not create staging directory "%STAGE_DIR%".
    goto :fail
)
robocopy /e /r:2 /w:1 "%DIST_DIR%\%APP_NAME%" "%STAGE_DIR%" >nul
if errorlevel 8 (
    echo ERROR: Could not copy PyInstaller output into staging.
    goto :fail
)
copy /y "%SCRIPT_DIR%\AppxManifest.xml" "%STAGE_DIR%\AppxManifest.xml" >nul
if errorlevel 1 (
    echo ERROR: Could not copy AppxManifest.xml.
    goto :fail
)
robocopy /e /r:2 /w:1 "%SCRIPT_DIR%\Assets" "%STAGE_DIR%\Assets" >nul
if errorlevel 8 (
    echo ERROR: Could not copy package assets into staging.
    goto :fail
)
copy /y "%VIDEO_DLL%" "%STAGE_DIR%\WallpaperVideoEngine.dll" >nul
if errorlevel 1 (
    echo ERROR: Could not copy WallpaperVideoEngine.dll into staging.
    goto :fail
)
if not exist "%STAGE_DIR%\WallpaperBar.exe" (
    echo ERROR: The staged application executable is missing.
    goto :fail
)
if not exist "%STAGE_DIR%\WallpaperVideoEngine.dll" (
    echo ERROR: The staged video engine DLL is missing.
    goto :fail
)
for %%I in ("%VIDEO_DLL%") do set "SOURCE_DLL_SIZE=%%~zI"
for %%I in ("%STAGE_DIR%\WallpaperVideoEngine.dll") do if not "%%~zI"=="%SOURCE_DLL_SIZE%" (
    echo ERROR: The staged video engine DLL size does not match the selected source DLL.
    goto :fail
)
for %%F in (Assets\StoreLogo.png Assets\Square150x150Logo.png Assets\Square44x44Logo.png) do (
    if not exist "%STAGE_DIR%\%%F" (
        echo ERROR: A required package asset is missing from staging: "%STAGE_DIR%\%%F"
        goto :fail
    )
)
if not exist "%STAGE_DIR%\style.css" if not exist "%STAGE_DIR%\_internal\style.css" (
    echo ERROR: style.css is missing from the staged PyInstaller resources.
    goto :fail
)

echo.
echo [3/4] Packing the MSIX with "%MAKEAPPX%"...
if exist "%TEMP_MSIX%" del /f /q "%TEMP_MSIX%"
if exist "%TEMP_MSIX%" (
    echo ERROR: Could not remove stale temporary package "%TEMP_MSIX%".
    goto :fail
)
"%MAKEAPPX%" pack /d "%STAGE_DIR%" /p "%TEMP_MSIX%" /o
if errorlevel 1 (
    echo ERROR: MakeAppx failed. Review its output above.
    goto :fail
)
if not exist "%TEMP_MSIX%" (
    echo ERROR: MakeAppx returned success but the temporary MSIX file is missing.
    goto :fail
)
for %%I in ("%TEMP_MSIX%") do if %%~zI LEQ 0 (
    echo ERROR: The generated MSIX is empty.
    goto :fail
)
move /y "%TEMP_MSIX%" "%OUTPUT_MSIX%" >nul
if errorlevel 1 (
    echo ERROR: The package was built, but could not be moved into its final output path.
    goto :fail
)

echo.
echo [4/4] Release package created:
echo "%OUTPUT_MSIX%"
echo The package is unsigned. Sign it with a certificate whose subject matches the Publisher in AppxManifest.xml.
goto :success

:python_override_failed
echo ERROR: WBP_PYTHON must point to an interpreter with PyInstaller, PySide6, and keyboard installed.
goto :fail

:fail
echo.
echo MSIX build failed. No success status was reported.
popd
exit /b 1

:fatal_no_pushd
echo ERROR: Could not enter the packaging directory "%~dp0".
exit /b 1

:success
popd
exit /b 0

:check_python
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import sys; assert sys.version_info >= (3, 10) and sys.maxsize > 2**32; import PyInstaller, PySide6, keyboard" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:check_x64_pe
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import struct,sys; f=open(sys.argv[1], 'rb'); f.seek(0x3c); offset=struct.unpack('<I',f.read(4))[0]; f.seek(offset+4); machine=struct.unpack('<H',f.read(2))[0]; assert machine==0x8664" "%~1" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
