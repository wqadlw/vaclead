@echo off
REM vaclead 质量门禁：后端 pytest + 前端构建
setlocal
cd /d %~dp0..

echo ==== backend pytest ====
cd backend
python -m pytest tests -v
if errorlevel 1 exit /b 1

echo ==== frontend build ====
cd ..\frontend
call npm run build
if errorlevel 1 exit /b 1

echo ALL GATES PASSED
