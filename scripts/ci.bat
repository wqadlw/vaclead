@echo off
REM vaclead CI gate: backend tests + frontend build/export + import smoke
setlocal
cd /d "%~dp0.."

echo [1/3] backend pytest...
cd backend
python -m pytest -q || goto :fail

echo [2/3] frontend build + static export...
cd ..\frontend
call npx next build || goto :fail
if not exist out\index.html goto :fail

echo [3/3] backend import smoke...
cd ..\backend
python -c "from app.main import app; print('app OK')" || goto :fail

echo CI PASSED
exit /b 0

:fail
echo CI FAILED
exit /b 1
