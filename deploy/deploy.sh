#!/usr/bin/env bash
# Pull the latest image on the VM and restart. Run from your Mac after the GitHub build is green.
#   deploy/deploy.sh            # uses VM/ZONE below or env overrides
set -euo pipefail
VM="${VM:-youtrained}"
ZONE="${ZONE:-us-central1-a}"
gcloud compute ssh "$VM" --zone "$ZONE" --command "
  sudo systemctl restart youtrained
  for i in \$(seq 1 90); do curl -s -o /dev/null http://127.0.0.1:8080/ && break; sleep 2; done
  sudo systemctl is-active youtrained
  curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:8080/
  sudo docker inspect youtrained --format 'image: {{.Image}}' | cut -c1-26
"
