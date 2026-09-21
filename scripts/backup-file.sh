#!/usr/bin/env bash
set -euo pipefail
FILE="$1"
STAMP="$(date -u +%Y-%m-%dT%H%M%S)"
DEST="/opt/manthan-backups/$STAMP"
mkdir -p "$DEST/$(dirname "$FILE")"
cp "/opt/manthan/$FILE" "$DEST/$FILE"
ln -sfn "$DEST" /opt/manthan-backups/latest
echo "Backed up $FILE -> $DEST/$FILE"
