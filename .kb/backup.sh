#!/usr/bin/env bash
# Backup notes/ and references/ (gitignored content)
# Usage: .kb/backup.sh [destination_dir]
#
# Default: creates timestamped tar.gz in ~/.kb-backups/
# Custom:  .kb/backup.sh /path/to/backup/dir

set -euo pipefail

KB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/.kb-backups}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="$DEST/kb-backup-$TIMESTAMP.tar.gz"

mkdir -p "$DEST"

cd "$KB_DIR"
tar -czf "$BACKUP_FILE" \
    notes/ \
    references/ \
    .kb/index/ \
    .kb/log.md \
    publish/ \
    2>/dev/null || true

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
COUNT=$(ls "$DEST"/kb-backup-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')

echo "Backed up: $BACKUP_FILE ($SIZE)"
echo "Total backups: $COUNT"

# Keep last 10 backups, delete older
if [ "$COUNT" -gt 10 ]; then
    ls -t "$DEST"/kb-backup-*.tar.gz | tail -n +11 | xargs rm -f
    echo "Pruned to 10 most recent backups"
fi
