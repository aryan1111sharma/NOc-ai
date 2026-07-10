#!/usr/bin/env bash
# Start the Adore Manthan.AI stack with the version + commit id wired into the
# LibreChat footer. Reads the version from ../VERSION and the commit from
# `git rev-parse --short HEAD`, then runs `docker compose up -d --build`.
#
# Usage:
#   ./up.sh              # build + start everything
#   ./up.sh api          # (extra args are passed through to docker compose up)
set -euo pipefail

chat_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$chat_dir")"

export APP_VERSION="$(tr -d '[:space:]' < "$root/VERSION")"
export GIT_SHA="$(git -C "$root" rev-parse --short HEAD)"

echo "Starting Adore Manthan.AI v${APP_VERSION} (${GIT_SHA})"
cd "$chat_dir"
docker compose up -d --build "$@"
echo "Open http://localhost:3080  — footer shows v${APP_VERSION} (${GIT_SHA})"
