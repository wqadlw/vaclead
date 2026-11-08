#!/usr/bin/env bash
# vaclead demo 服务器装配（root，幂等，可重复执行）。
#
# 前置：本地已上传 /tmp/vaclead-demo.tgz（白名单打包，见 demo_pack_and_upload），
#       DNS 已解析 vaclead.raozewei.cn → 47.103.153.93。
#
# 做的事：
#   1. 解包 → /opt/vaclead-demo（布局 backend/app + frontend/out + config/，
#      main.py 的 _STATIC_DIR 相对路径依赖此布局）
#   2. venv + pip install -r backend/requirements.txt（已存在则跳过）
#   3. 首次播种：data-demo/demo.db.master → data/demo.db
#   4. systemd 单元 vaclead-demo.service（127.0.0.1:8801，禁定时任务，随机 JWT）
#      ——幂等：JWT_SECRET 沿用旧单元的值，避免每次装配把全部会话踢下线
#   5. cron 每日 04:10 重置运行库（demo_reset.sh）
#   6. nginx vhost（HTTP 先行，证书签发成功后升级 443）
#   7. acme.sh 签发 vaclead.raozewei.cn 证书（DNS 未生效则跳过，重跑即可）
#
# 验证：curl http://127.0.0.1:8801/ → 200；公网 https://vaclead.raozewei.cn → 200
set -euo pipefail

APP=/opt/vaclead-demo
TGZ=/tmp/vaclead-demo.tgz
PORT=8801
DOMAIN=vaclead.raozewei.cn
ACME_WEBROOT=/www/wwwroot/vaclead-raozewei-acme
CERT_DIR=/www/server/panel/vhost/cert/vaclead.raozewei.cn
NGX_CONF=/www/server/panel/vhost/nginx/vaclead.raozewei.cn.conf

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[[ -f "$TGZ" ]] || { echo "✗ 找不到 $TGZ（先在本地打包上传）"; exit 1; }

step "解包 → $APP"
mkdir -p "$APP"
tar -xzf "$TGZ" -C "$APP"
mkdir -p "$APP/data"

step "venv + 依赖（首次约 3~6 分钟）"
if [[ ! -d "$APP/venv" ]]; then
  python3 -m venv "$APP/venv"
  "$APP/venv/bin/pip" install -q --upgrade pip
fi
"$APP/venv/bin/pip" install -q -r "$APP/backend/requirements.txt"

step "首次播种"
if [[ ! -f "$APP/data/demo.db" ]]; then
  cp "$APP/data-demo/demo.db.master" "$APP/data/demo.db"
  echo "  已从 master 播种 demo.db"
else
  echo "  demo.db 已存在，跳过（每日 04:10 cron 会用 master 重置）"
fi

step "systemd 单元"
# 幂等：沿用旧 JWT_SECRET
OLD_SECRET=""
[[ -f /etc/systemd/system/vaclead-demo.service ]] && \
  OLD_SECRET="$(grep -oP 'VACLEAD_JWT_SECRET=\K.*' /etc/systemd/system/vaclead-demo.service || true)"
SECRET="${OLD_SECRET:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
cat > /etc/systemd/system/vaclead-demo.service <<UNIT
[Unit]
Description=vaclead demo (FastAPI single-process, localhost only)
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP/backend
ExecStart=$APP/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT
Environment=VACLEAD_DB=$APP/data/demo.db
Environment=VACLEAD_DISABLE_JOBS=1
Environment=VACLEAD_CONFIG=$APP/config/industrial.yaml
Environment=VACLEAD_JWT_SECRET=$SECRET
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now vaclead-demo

step "cron 每日 04:10 重置"
( crontab -l 2>/dev/null | grep -v 'vaclead-demo-reset' ; \
  echo '10 4 * * * /usr/bin/env bash /opt/vaclead-demo/scripts/demo_reset.sh >> /www/wwwlogs/vaclead-demo-reset.log 2>&1 # vaclead-demo-reset' \
) | crontab -
crontab -l | grep vaclead-demo-reset

step "nginx vhost（HTTP 先行）"
mkdir -p "$ACME_WEBROOT"
cat > "$NGX_CONF" <<NGINX
server {
    listen 80;
    server_name $DOMAIN;
    access_log /www/wwwlogs/vaclead.raozewei.cn.log;

    location ^~ /.well-known/acme-challenge/ { root $ACME_WEBROOT; }

    # demo 拦截：采集任务 / 导入 / 群发 / 机器通道（防游客触发外呼与灌库）
    location ~ ^/api/(tasks|import|outreach|mcp)(/|\$) { return 403; }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        client_max_body_size 20m;
    }
}
NGINX
nginx -t && systemctl reload nginx

step "acme.sh 签发证书"
CERT_OK=0
if [[ -f "$CERT_DIR/fullchain.pem" ]]; then
  echo "  证书已存在，跳过签发"
  CERT_OK=1
elif command -v ~/.acme.sh/acme.sh >/dev/null 2>&1; then
  if ~/.acme.sh/acme.sh --issue -d "$DOMAIN" -w "$ACME_WEBROOT" --server letsencrypt; then
    mkdir -p "$CERT_DIR"
    ~/.acme.sh/acme.sh --install-cert -d "$DOMAIN" \
      --fullchain-file "$CERT_DIR/fullchain.pem" \
      --key-file "$CERT_DIR/privkey.pem" \
      --reloadcmd "systemctl reload nginx"
    CERT_OK=1
  else
    echo "  ⚠️ 签发失败（多半是 DNS 未生效）。解析生效后重跑本脚本即可。"
  fi
else
  echo "  ⚠️ 未找到 acme.sh（~/.acme.sh/acme.sh）。请先安装或参照 demo.raozewei.cn 的签发方式。"
fi

if [[ "$CERT_OK" == "1" ]]; then
  step "升级 443"
  cat > "$NGX_CONF" <<NGINX
server {
    listen 80;
    server_name $DOMAIN;
    access_log /www/wwwlogs/vaclead.raozewei.cn.log;
    location ^~ /.well-known/acme-challenge/ { root $ACME_WEBROOT; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    access_log /www/wwwlogs/vaclead.raozewei.cn.log;
    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;

    location ~ ^/api/(tasks|import|outreach|mcp)(/|\$) { return 403; }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        client_max_body_size 20m;
    }
}
NGINX
  nginx -t && systemctl reload nginx
  echo "  443 已启用"
fi

step "探活"
sleep 4
printf '服务: %s\n' "$(systemctl is-active vaclead-demo)"
printf '本机探活: HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/)"
printf '拦截验证(应403): HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:$PORT/api/tasks/ccgp-scan)"
printf '登录页(应200): HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/login)"

echo
echo "===== 完成。公网入口 https://$DOMAIN （等 DNS 生效后即可访问）====="
