@echo off
setlocal

set "TOOL_SCRIPT=%~dp0FBX_Exporter.py"
set "MAYAPY_PATH="

if defined MAYA_LOCATION if exist "%MAYA_LOCATION%\bin\mayapy.exe" (
    set "MAYAPY_PATH=%MAYA_LOCATION%\bin\mayapy.exe"
)

if not defined MAYAPY_PATH if exist "C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" (
    set "MAYAPY_PATH=C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe"
)

if not defined MAYAPY_PATH (
    for /d %%D in ("C:\Program Files\Autodesk\Maya20*") do (
        if exist "%%~fD\bin\mayapy.exe" set "MAYAPY_PATH=%%~fD\bin\mayapy.exe"
    )
)

if not exist "%TOOL_SCRIPT%" (
    echo Cannot find: %TOOL_SCRIPT%
    pause
    exit /b 1
)

if not defined MAYAPY_PATH (
    echo Cannot find mayapy.exe. Install Maya or set MAYA_LOCATION.
    pause
    exit /b 1
)

set "FBX_EXPORTER_SHOW_CONSOLE=1"
start "" /min "%MAYAPY_PATH%" "%TOOL_SCRIPT%" %*

endlocal
