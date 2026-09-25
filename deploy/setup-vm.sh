#!/usr/bin/env bash
# One-off VM setup for a Debian 12 e2-micro. Run as root: sudo bash setup-vm.sh <ghcr image repo>
# e.g. sudo bash setup-vm.sh mattthurling/youtrained
set -euo pipefail
IMAGE_REPO="${1:?usage: setup-vm.sh <owner/repo>}"

# 2 GB swap: 1 GB RAM is tight for PDF rendering and any one-off DB work.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https zstd sqlite3

# Docker (official repo)
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list
  apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io
fi

# Caddy (official repo)
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi

mkdir -p /opt/youtrained/data
if [ ! -f /opt/youtrained/.env ]; then
  cat > /opt/youtrained/.env <<ENV
IMAGE_REPO=${IMAGE_REPO}
YOUTUBE_API_KEY=
ENV
  chmod 600 /opt/youtrained/.env
  echo ">>> add YOUTUBE_API_KEY to /opt/youtrained/.env"
fi

cp "$(dirname "$0")/youtrained.service" /etc/systemd/system/youtrained.service
cp "$(dirname "$0")/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable youtrained
systemctl restart caddy
echo "setup done. Start the app with: systemctl start youtrained"
