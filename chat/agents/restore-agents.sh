#!/usr/bin/env sh
# Restore LibreChat agents from agents.json into MongoDB (idempotent upsert). Run after a fresh
# `docker compose up` once the owning admin user has been registered. Requires the mongo
# container (noc-librechat-mongo) up. See chat/RUNBOOK.md.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
docker cp "$DIR/agents.json" noc-librechat-mongo:/tmp/agents.restore.json
docker exec -i noc-librechat-mongo mongosh --quiet LibreChat --file /dev/stdin < "$DIR/_restore.js"
docker exec noc-librechat-mongo rm -f /tmp/agents.restore.json
