#!/usr/bin/env bash
# One-time server setup + deploy on Ubuntu 22.04/24.04 (x86_64 or ARM, e.g. Oracle Cloud Ampere A1).
# Run on the server as the default user (needs sudo):  bash setup_server.sh
# Expects ~/hospital-ai-assistant/.env to exist already (copied with scp; never committed).
set -euo pipefail
REPO_URL="https://github.com/sptiwari296/hospital-ai-assistant.git"
APP_DIR="$HOME/hospital-ai-assistant"

echo "== Docker (from Ubuntu's own repositories)"
if ! command -v docker >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q docker.io docker-compose-v2 git
  sudo systemctl enable --now docker
fi
sudo usermod -aG docker "$USER" || true

echo "== Firewall: allow HTTP/HTTPS (Oracle's Ubuntu images block everything except SSH by default)"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
if command -v netfilter-persistent >/dev/null; then sudo netfilter-persistent save; fi

echo "== Code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  # the directory may already exist because .env was copied into it first
  git clone "$REPO_URL" /tmp/hospital-src && cp -rn /tmp/hospital-src/. "$APP_DIR"/ && rm -rf /tmp/hospital-src
fi
test -f "$APP_DIR/.env" || { echo "Missing $APP_DIR/.env - copy it first"; exit 1; }
chmod 600 "$APP_DIR/.env"

echo "== Build and start (first build ~10-15 min: Python packages + AI models)"
cd "$APP_DIR"
sudo docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d --build
sudo docker compose -f docker-compose.yml -f docker-compose.cloud.yml ps
