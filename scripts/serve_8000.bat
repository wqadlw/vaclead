@echo off
REM 单进程演示启动：FastAPI :8000 托管前端 out/ + 同源 API
REM 清代理环境变量——采集器(httpx trust_env)继承死代理会导致夜间 cron 全挂
cd /d E:\AAAA\A-xiaosou\vaclead\backend
set VACLEAD_DISABLE_JOBS=0
set http_proxy=
set https_proxy=
set HTTP_PROXY=
set HTTPS_PROXY=
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > ..\data\server_8000.log 2>&1
