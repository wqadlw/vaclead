@echo off
REM ============================================
REM vaclead 一键启动（双击运行）
REM 会弹出一个黑色控制台窗口——请保持窗口开启（最小化即可），关闭窗口=停止服务
REM 浏览器访问 http://127.0.0.1:8000 （admin / admin123）
REM ============================================
cd /d E:\AAAA\A-xiaosou\vaclead\backend
set VACLEAD_DISABLE_JOBS=0
set http_proxy=
set https_proxy=
set HTTP_PROXY=
set HTTPS_PROXY=
echo 正在启动 vaclead（关闭本窗口 = 停止服务）...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
