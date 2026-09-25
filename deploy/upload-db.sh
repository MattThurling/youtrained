#!/usr/bin/env bash
# Copy the local SQLite database to the VM, compressed, and swap it in atomically.
#   deploy/upload-db.sh
set -euo pipefail
VM="${VM:-youtrained}"
ZONE="${ZONE:-us-central1-a}"
SRC="${1:-data/youtrained.sqlite}"
TMP="$(mktemp -d)"
echo "checkpointing WAL and snapshotting..."
sqlite3 "$SRC" "PRAGMA wal_checkpoint(TRUNCATE); VACUUM INTO '$TMP/youtrained.sqlite';"
echo "compressing..."
zstd -T0 -3 --rm "$TMP/youtrained.sqlite" -o "$TMP/youtrained.sqlite.zst"
ls -lh "$TMP/youtrained.sqlite.zst"
echo "uploading..."
gcloud compute scp "$TMP/youtrained.sqlite.zst" "$VM:/tmp/youtrained.sqlite.zst" --zone "$ZONE"
gcloud compute ssh "$VM" --zone "$ZONE" --command "set -e; cd /opt/youtrained/data && sudo zstd -d -f /tmp/youtrained.sqlite.zst -o youtrained.sqlite.new && sudo systemctl stop youtrained && sudo rm -f youtrained.sqlite youtrained.sqlite-wal youtrained.sqlite-shm && sudo mv youtrained.sqlite.new youtrained.sqlite && sudo systemctl start youtrained && rm /tmp/youtrained.sqlite.zst && echo swapped"
rm -rf "$TMP"
