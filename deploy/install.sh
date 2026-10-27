#!/usr/bin/env bash
# Bare-metal install (Debian/Ubuntu): sudo bash deploy/install.sh
set -euo pipefail
DIR=/opt/tgapi
apt-get update -qq && apt-get install -y -qq python3 python3-venv rsync >/dev/null
id tgapi &>/dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin tgapi
mkdir -p "$DIR/data"
rsync -a --exclude data --exclude .venv --exclude .git ./ "$DIR/"
[ -f "$DIR/.env" ] || cp .env.example "$DIR/.env"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
chown -R tgapi:tgapi "$DIR"
chmod 600 "$DIR/.env"
cp deploy/tgapi.service /etc/systemd/system/tgapi.service
systemctl daemon-reload
systemctl enable --now tgapi
systemctl restart tgapi
echo "OK: http://127.0.0.1:$(grep -E '^PORT=' "$DIR/.env" | cut -d= -f2 || echo 8080)  — put nginx (deploy/nginx.conf) + HTTPS in front."
