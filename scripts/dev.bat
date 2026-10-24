@echo off
REM vaclead 一键启动：后端 uvicorn(:8000) + 前端 next dev(:3000)
setlocal
cd /d %~dp0..

start "vaclead-backend" cmd /c "cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
start "vaclead-frontend" cmd /c "cd frontend && npm run dev"

echo 后端 http://127.0.0.1:8000/docs  前端 http://127.0.0.1:3000
echo 账号 admin/admin123 或 sales/sales123
