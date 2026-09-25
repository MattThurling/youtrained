#!/usr/bin/env bash
# Toggle the holding page at Caddy on the VM.
#   deploy/holding.sh on     # everyone sees the holding page (503); prints your preview link
#   deploy/holding.sh off    # back to the app
set -euo pipefail
VM="${VM:-youtrained}"; ZONE="${ZONE:-us-central1-a}"
HERE="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  on)
    SECRET="$(openssl rand -hex 12)"
    TMP="$(mktemp)"
    cat > "$TMP" <<CADDY
youtrained.com, www.youtrained.com {
    encode zstd gzip

    # Visiting this link once sets a cookie that bypasses the holding page for 30 days.
    @preview path /preview/${SECRET}
    handle @preview {
        header +Set-Cookie "yt_preview=${SECRET}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000"
        redir / 302
    }

    @allowed header_regexp Cookie yt_preview=${SECRET}
    handle @allowed {
        reverse_proxy 127.0.0.1:8080
    }

    handle {
        header Retry-After 3600
        root * /opt/youtrained/holding
        rewrite * /index.html
        file_server {
            status 503
        }
    }
}
CADDY
    gcloud compute scp "$TMP" "$HERE/holding.html" "$VM:/tmp/" --zone "$ZONE" --quiet
    gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command "sudo mkdir -p /opt/youtrained/holding && sudo mv /tmp/holding.html /opt/youtrained/holding/index.html && sudo mv /tmp/$(basename "$TMP") /etc/caddy/Caddyfile && sudo chown root:root /etc/caddy/Caddyfile /opt/youtrained/holding/index.html && sudo chmod 644 /etc/caddy/Caddyfile /opt/youtrained/holding/index.html && sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null && sudo systemctl reload caddy && echo 'holding page ON'"
    rm -f "$TMP"
    echo "preview link (keep private): https://youtrained.com/preview/${SECRET}"
    ;;
  off)
    gcloud compute scp "$HERE/Caddyfile" "$VM:/tmp/Caddyfile" --zone "$ZONE" --quiet
    gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command "sudo mv /tmp/Caddyfile /etc/caddy/Caddyfile && sudo chown root:root /etc/caddy/Caddyfile && sudo chmod 644 /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null && sudo systemctl reload caddy && echo 'holding page OFF'"
    ;;
  *) echo "usage: $0 on|off"; exit 2 ;;
esac
