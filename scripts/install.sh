#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo bash scripts/install.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/pattern-decision"
WEB_DIR="/var/www/pattern-decision"
STATE_DIR="/var/lib/pattern-decision"

for required in "$SOURCE_DIR/site/index.html" \
                "$SOURCE_DIR/tools/update_pattern_decision_live.py" \
                "$SOURCE_DIR/data/pattern_decision_data.json" \
                "$SOURCE_DIR/config/market_watchlist.json"; do
  if [[ ! -f "$required" ]]; then
    echo "缺少部署文件：$required" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3，请先安装：sudo apt-get install -y python3" >&2
  exit 1
fi
if ! command -v nginx >/dev/null 2>&1; then
  echo "缺少 nginx，请先安装：sudo apt-get install -y nginx" >&2
  exit 1
fi

if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
  echo "同步程序到 $APP_DIR ..."
  install -d -m 0755 "$APP_DIR"
  command -v rsync >/dev/null 2>&1 || {
    echo "缺少 rsync，请先安装：sudo apt-get install -y rsync" >&2
    exit 1
  }
  rsync -a --delete \
    --exclude='.git/' \
    --exclude='.pytest_cache/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='docs/data/snapshots/*' \
    --exclude='state/*.json' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi

echo "部署静态页面到 $WEB_DIR ..."
install -d -o www-data -g www-data -m 0755 "$WEB_DIR" "$STATE_DIR"
find "$WEB_DIR" -maxdepth 1 -type f -name '*.html' -delete
cp -a "$APP_DIR/site/." "$WEB_DIR/"
chown -R www-data:www-data "$WEB_DIR" "$STATE_DIR"

echo "安装 systemd 刷新任务 ..."
install -m 0644 "$APP_DIR/deploy/pattern-decision-live.service" /etc/systemd/system/pattern-decision-live.service
install -m 0644 "$APP_DIR/deploy/pattern-decision-live.timer" /etc/systemd/system/pattern-decision-live.timer
systemctl daemon-reload

echo "安装 Nginx 配置 ..."
install -m 0644 "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/pattern-decision

DEFAULT_LINK="/etc/nginx/sites-enabled/default"
if [[ -e "$DEFAULT_LINK" ]]; then
  BACKUP_LINK="${DEFAULT_LINK}.pattern-decision-disabled.$(date +%Y%m%d%H%M%S)"
  echo "为避免 80 端口默认站点冲突，把原 default 配置移动到：$BACKUP_LINK"
  mv "$DEFAULT_LINK" "$BACKUP_LINK"
fi

ln -sfn /etc/nginx/sites-available/pattern-decision /etc/nginx/sites-enabled/pattern-decision
nginx -t
systemctl reload nginx

systemctl enable --now pattern-decision-live.timer
if systemctl start pattern-decision-live.service; then
  echo "首次实时刷新完成。"
else
  echo "首次实时刷新失败；静态页面已保留，请查看 journalctl -u pattern-decision-live.service" >&2
fi

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "部署完成：http://${SERVER_IP:-YOUR_SERVER_IP}/"
echo "旧路径兼容：http://${SERVER_IP:-YOUR_SERVER_IP}/intel/"
echo "定时任务：systemctl list-timers pattern-decision-live.timer"
