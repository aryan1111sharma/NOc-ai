#!/usr/bin/env bash
set -euo pipefail
FILE="$1"
SNAPSHOT="${2:-latest}"
SRC="/opt/manthan-backups/$SNAPSHOT/$FILE"
[ -f "$SRC" ] || { echo "No backup of $FILE in $SNAPSHOT"; exit 1; }
cp "$SRC" "/opt/manthan/$FILE"
echo "Restored $FILE from $SNAPSHOT"
