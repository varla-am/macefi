@echo off
rem install.bat - puts a `macefi` command on PATH (Windows).
rem %LOCALAPPDATA%\Microsoft\WindowsApps is on the user PATH by default, so no PATH edits.
setlocal
set "MAIN=%~dp0mainscript.py"
if not exist "%MAIN%" (
  echo error: %MAIN% not found ^(keep install.bat next to mainscript.py^)
  exit /b 1
)

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo error: Python 3.9+ is required ^(https://www.python.org/downloads/^)
  exit /b 1
)

set "SHIM=%LOCALAPPDATA%\Microsoft\WindowsApps\macefi.cmd"
> "%SHIM%" echo @%PY% "%MAIN%" %%*
echo Installed: %SHIM%
echo Open a new terminal, then:  macefi detect -o hw.json
endlocal
