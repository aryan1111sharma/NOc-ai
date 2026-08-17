#!/usr/bin/env sh
# Export LibreChat agents to agents.json (config-as-code). Run after changing an agent in the
# UI so the change is captured in git. Requires the mongo container (noc-librechat-mongo) up.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
docker exec -i noc-librechat-mongo mongosh --quiet LibreChat --file /dev/stdin \
  < "$DIR/_export.js" > "$DIR/agents.json"
echo "Exported agents -> $DIR/agents.json"
