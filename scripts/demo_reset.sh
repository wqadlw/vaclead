#!/usr/bin/env bash
# vaclead demo 每日重置：运行库 ← master 副本 + 重启服务。
# cron 每日 04:10 调用；也可手动触发验证。
#
# 用法：bash /opt/vaclead-demo/scripts/demo_reset.sh
set -e
APP=/opt/vaclead-demo

systemctl stop vaclead-demo || true
cp "$APP/data-demo/demo.db.master" "$APP/data/demo.db"
rm -f "$APP/data/demo.db-wal" "$APP/data/demo.db-shm"
systemctl start vaclead-demo
# uvicorn 启动 + 28MB 库 init_db 需要数秒，轮询而不是固定 sleep 3（2026-11-09 实测 3 秒会误报 000）
for _ in 1 2 3 4 5 6; do
  sleep 2
  CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8801/ || echo 000)
  [[ "$CODE" == "200" ]] && break
done
printf '服务: %s  探活: %s\n' \
  "$(systemctl is-active vaclead-demo)" "$CODE"
