#!/usr/bin/env pwsh
# Restore LibreChat agents from agents.json into MongoDB (idempotent upsert). Run after a fresh
# `docker compose up` once the owning admin user has been registered. Requires the mongo
# container (noc-librechat-mongo) up. See apps/ai-platform/librechat/RUNBOOK.md.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
docker cp "$dir\agents.json" noc-librechat-mongo:/tmp/agents.restore.json
Get-Content "$dir\_restore.js" -Raw | docker exec -i noc-librechat-mongo mongosh --quiet LibreChat --file /dev/stdin
docker exec noc-librechat-mongo rm -f /tmp/agents.restore.json
