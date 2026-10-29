@echo off
REM vaclead CI gate: backend tests + frontend build/export + import smoke
setlocal
cd /d "%~dp0.."

echo [1/4] backend pytest...
cd backend
python -m pytest -q || goto :fail

echo [2/4] frontend build + static export...
cd ..\frontend
call npx next build || goto :fail
if not exist out\index.html goto :fail

echo [3/4] E2E tests...
cd ..\frontend
REM 系统代理(Clash)会把 playwright webServer 就绪探针(127.0.0.1:8010)劫成 502 → 永不就绪
set NO_PROXY=127.0.0.1,localhost
call npx playwright test || goto :fail

echo [4/4] backend import smoke...
cd ..\backend
python -c "from app.main import app; print('app OK')" || goto :fail

echo CI PASSED
exit /b 0

:fail
echo CI FAILED
exit /b 1
