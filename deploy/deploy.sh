#!/usr/bin/env bash
# Pull the latest image on the VM and restart. Run from your Mac after the GitHub build is green.
#   deploy/deploy.sh            # uses VM/ZONE below or env overrides
set -euo pipefail
VM="${VM:-youtrained}"
ZONE="${ZONE:-us-central1-a}"
gcloud compute ssh "$VM" --zone "$ZONE" --command "sudo systemctl restart youtrained && sleep 3 && sudo systemctl is-active youtrained && curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:8080/"
