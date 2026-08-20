@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
  ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
  if errorlevel 1 goto :venv_error
  start "" ".venv\Scripts\pythonw.exe" "installer\installer_gui_v0001.py"
  exit /b 0
)

where py.exe >nul 2>nul
if not errorlevel 1 (
  py.exe -3.12 -c "import tkinter" >nul 2>nul
  if errorlevel 1 goto :python_error
  where pyw.exe >nul 2>nul
  if errorlevel 1 goto :python_error
  start "" pyw.exe -3.12 "installer\installer_gui_v0001.py"
  exit /b 0
)

:python_error
echo [ERROR] Python 3.12 was not found.
echo Install Python 3.12 with tkinter, enable the Python Launcher, then run this file again.
pause
exit /b 1

:venv_error
echo [ERROR] The existing .venv cannot import tkinter.
echo Recreate the virtual environment with a standard Python 3.12 installation.
pause
exit /b 1
