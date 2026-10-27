#!/usr/bin/env bash
# Ship the systemd unit and Caddyfile, pull the latest image on the VM and restart.
# Run from your Mac after the GitHub build is green.
#   deploy/deploy.sh            # uses VM/ZONE/PROJECT below or env overrides
set -euo pipefail
VM="${VM:-youtrained}"
ZONE="${ZONE:-us-central1-a}"
PROJECT="${PROJECT:-suenow}"
HERE="$(cd "$(dirname "$0")" && pwd)"
GC=(gcloud compute --project "$PROJECT")

"${GC[@]}" scp "$HERE/youtrained.service" "$HERE/Caddyfile" "$VM:/tmp/" --zone "$ZONE" --quiet
"${GC[@]}" ssh "$VM" --zone "$ZONE" --quiet --command "
  set -e
  sudo install -m 644 -o root -g root /tmp/youtrained.service /etc/systemd/system/youtrained.service
  sudo install -m 644 -o root -g root /tmp/Caddyfile /etc/caddy/Caddyfile
  rm -f /tmp/youtrained.service /tmp/Caddyfile
  sudo systemctl daemon-reload
  sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null && sudo systemctl reload caddy
  sudo sh -c '. /opt/youtrained/.env && docker pull ghcr.io/\${IMAGE_REPO}:latest'
  sudo systemctl restart youtrained
  for i in \$(seq 1 90); do curl -s -o /dev/null http://127.0.0.1:8080/ && break; sleep 2; done
  sudo systemctl is-active youtrained
  curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:8080/
  sudo docker inspect youtrained --format 'image: {{.Image}} mem: {{.HostConfig.Memory}} swap: {{.HostConfig.MemorySwap}}' | cut -c1-80
"
