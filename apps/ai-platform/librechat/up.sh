#!/usr/bin/env bash
# Start the Ador Manthan.AI stack with the version + commit id wired into the
# LibreChat footer. Reads the version from ../VERSION and the commit from
# `git rev-parse --short HEAD`, then runs `docker compose up -d --build`.
#
# LibreChat inspects each MCP server exactly once, at its own startup. A rebuild
# recreates the MCP containers but leaves the api container running, so without a
# follow-up restart LibreChat keeps handles on containers that no longer exist and
# comes back reporting 0 tools - every integration silently missing from the UI,
# with no error shown to the user. So once the build is done we wait for the MCP
# servers to accept connections and then restart api, in that order.
#
# Usage:
#   ./up.sh              # build + start everything, then wait + restart api
#   ./up.sh api          # extra args pass through to docker compose up; the
#                        # wait + restart step is skipped
set -euo pipefail

chat_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Repo root is 3 levels up: apps/ai-platform/librechat -> apps/ai-platform -> apps -> repo root.
root="$(dirname "$(dirname "$(dirname "$chat_dir")")")"

export APP_VERSION="$(tr -d '[:space:]' < "$root/VERSION")"
export GIT_SHA="$(git -C "$root" rev-parse --short HEAD)"

# Read SERVER_HOST from .env so the closing "Open http://..." message is always right,
# instead of a hardcoded address that goes stale the moment the deployment moves host.
server_host="localhost"
if [ -f "$chat_dir/.env" ]; then
  val="$(grep -E '^SERVER_HOST=' "$chat_dir/.env" | tail -1 | cut -d= -f2-)"
  [ -n "$val" ] && server_host="$val"
fi

# compose service name : the port it listens on inside the compose network.
mcp_servers=(
  "mcp-server:8100"
  "cms-mcp-server:8101"
  "jira-mcp-server:8102"
  "docgen-mcp-server:8103"
  "ecog-sd-mcp-server:8104"
  "chargezone-jira-mcp-server:8105"
  "kb-mcp-server:8106"
  "platform-mcp-server:8107"
)
# kb-mcp-server downloads its embedding models on first run, which can take
# several minutes - hence the generous budget.
ready_timeout_seconds=600

echo "Starting Ador Manthan.AI v${APP_VERSION} (${GIT_SHA})"
cd "$chat_dir"
docker compose up -d --build "$@"

if [ "$#" -gt 0 ]; then
  echo "Extra args given - skipping the MCP readiness wait and the api restart."
else
  echo "Waiting for the MCP servers to accept connections..."
  deadline=$(( $(date +%s) + ready_timeout_seconds ))
  not_ready=()
  for entry in "${mcp_servers[@]}"; do
    service="${entry%%:*}"
    port="${entry##*:}"
    ready=0
    while [ "$(date +%s)" -lt "$deadline" ]; do
      if docker compose exec -T "$service" python -c \
        "import socket; socket.create_connection(('127.0.0.1', $port), 2).close()" >/dev/null 2>&1; then
        ready=1
        break
      fi
      sleep 5
    done
    if [ "$ready" -eq 1 ]; then
      echo "  ready: $service"
    else
      not_ready+=("$service")
      echo "  WARNING: NOT ready after ${ready_timeout_seconds}s: $service" >&2
    fi
  done

  # LibreChat must come up after the MCP servers, or it will hold dead handles
  # and report 0 tools.
  echo "Restarting LibreChat so it re-handshakes with the MCP servers..."
  docker compose restart api

  if [ "${#not_ready[@]}" -gt 0 ]; then
    echo "WARNING: these MCP servers never came up: ${not_ready[*]}." >&2
    echo "LibreChat will be missing their tools; check 'docker compose logs <service>'." >&2
  fi
fi

echo "Open http://${server_host}  - footer shows v${APP_VERSION} (${GIT_SHA})"
echo "Admin panel: http://${server_host}:3091"
echo "Verify the tool count with: docker compose logs api | grep 'Initialized with'"
