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
sleep 3
printf '服务: %s  探活: %s\n' \
  "$(systemctl is-active vaclead-demo)" \
  "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8801/)"
