@echo off
chcp 65001 >nul
title 微波智选 - 微波加热智能选矿系统
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" run_demo.py
  if errorlevel 1 pause
  exit /b
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_demo.py
  if errorlevel 1 pause
  exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
  python run_demo.py
  if errorlevel 1 pause
  exit /b
)

echo 未找到 Python 3，请先安装 Python 后再运行。
pause
