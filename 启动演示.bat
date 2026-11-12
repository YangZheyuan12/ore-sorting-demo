@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_demo.py
  if %errorlevel% neq 0 pause
  exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
  python run_demo.py
  if %errorlevel% neq 0 pause
  exit /b
)

echo 未找到 Python 3，请先安装 Python 后再运行。
pause
