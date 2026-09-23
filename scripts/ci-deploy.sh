#!/usr/bin/env bash
set -uo pipefail

# Prevent two deploys from ever running at once: if another deploy already holds
# this lock, exit immediately instead of racing it for the same git checkout and
# the same Docker containers.
LOCK_FILE="/opt/manthan/logs/.deploy.lock"
mkdir -p "$(dirname "$LOCK_FILE")"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "Another deploy is already running (lock held on $LOCK_FILE). Exiting without doing anything."
  exit 1
fi

REPO_DIR="/opt/manthan"
CHAT_DIR="$REPO_DIR/apps/ai-platform/librechat"
BRANCH="feat/mcp-server"
LOG_DIR="/opt/manthan/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y-%m-%dT%H%M%S)"
LOG_FILE="$LOG_DIR/deploy-$STAMP.log"

log() { echo "$(date -u +%H:%M:%S) $*" | tee -a "$LOG_FILE"; }

fail_stage() {
  local stage="$1"; shift
  local detail="$*"
  log "FAILED at stage: $stage"
  log "Detail: $detail"
  printf '%s\n' "$stage"  > "$LOG_DIR/last-failure-stage.txt"
  printf '%s\n' "$detail" > "$LOG_DIR/last-failure-detail.txt"
  printf '%s\n' "$STAMP"  > "$LOG_DIR/last-failure-timestamp.txt"
}

clear_failure_marker() {
  rm -f "$LOG_DIR/last-failure-stage.txt" "$LOG_DIR/last-failure-detail.txt" "$LOG_DIR/last-failure-timestamp.txt"
}

# The app takes 10-15s after a restart to finish connecting to all 8 internal
# tool servers before it can serve requests. A single immediate check sees a
# false-positive 502 every time and triggers an unnecessary rollback, so retry
# for up to 90s before deciding it's actually unhealthy.
wait_for_healthy() {
  local attempts=18 delay=5
  for ((i=1; i<=attempts; i++)); do
    login_code="$(curl -s -o /dev/null -w '%{http_code}' https://ai.manthanquench.online/login || echo 000)"
    admin_code="$(curl -s -o /dev/null -w '%{http_code}' https://admin.manthanquench.online/ || echo 000)"
    if [ "$login_code" = "200" ] && [ "$admin_code" = "200" ]; then
      return 0
    fi
    log "  attempt $i/$attempts: login -> $login_code, admin -> $admin_code (retrying in ${delay}s)"
    sleep "$delay"
  done
  return 1
}

cd "$REPO_DIR"
PREV_COMMIT="$(git rev-parse HEAD)"

log "=== Deploy started (log: $LOG_FILE) ==="

log "Stage: backup"
if ! "$REPO_DIR/scripts/backup-file.sh" apps/ai-platform/librechat/docker-compose.yml >>"$LOG_FILE" 2>&1; then
  fail_stage "backup" "Could not back up docker-compose.yml. Check the file exists at that path and is readable."
  exit 1
fi
if ! "$REPO_DIR/scripts/backup-file.sh" apps/ai-platform/librechat/nginx.conf >>"$LOG_FILE" 2>&1; then
  fail_stage "backup" "Could not back up nginx.conf. Check the file exists at that path and is readable."
  exit 1
fi

log "Stage: git-fetch"
if ! git fetch origin "$BRANCH" >>"$LOG_FILE" 2>&1; then
  fail_stage "git-fetch" "Could not fetch origin/$BRANCH. Likely no network from the server to GitHub, or the branch was deleted/renamed."
  exit 1
fi

log "Stage: git-checkout"
if ! git checkout "$BRANCH" >>"$LOG_FILE" 2>&1; then
  fail_stage "git-checkout" "Could not switch to $BRANCH. Likely uncommitted local changes on the server are blocking checkout (run 'git status' in $REPO_DIR)."
  exit 1
fi

log "Stage: git-merge"
if ! git merge --ff-only "origin/$BRANCH" >>"$LOG_FILE" 2>&1; then
  fail_stage "git-merge" "Fast-forward merge failed. The server's local branch has commits that diverge from origin/$BRANCH — needs manual reconciliation, not safe to auto-merge."
  exit 1
fi

NEW_COMMIT="$(git rev-parse HEAD)"
if [ "$PREV_COMMIT" = "$NEW_COMMIT" ]; then
  log "No changes ($PREV_COMMIT). Nothing to deploy."
  clear_failure_marker
  exit 0
fi

log "Stage: build ($PREV_COMMIT -> $NEW_COMMIT)"
cd "$CHAT_DIR"
if ! ./up.sh >>"$LOG_FILE" 2>&1; then
  build_tail="$(tail -n 30 "$LOG_FILE")"
  fail_stage "build" "up.sh failed while building/starting containers for commit $NEW_COMMIT. Last lines of build output:
$build_tail"
  log "Build failed — reverting checkout to previous commit $PREV_COMMIT and rebuilding that instead..."
  cd "$REPO_DIR" && git checkout "$PREV_COMMIT" >>"$LOG_FILE" 2>&1
  cd "$CHAT_DIR"
  if ./up.sh >>"$LOG_FILE" 2>&1; then
    log "Recovered: server is back on previous commit $PREV_COMMIT. $NEW_COMMIT never went live — fix the build error above before retrying."
  else
    fail_stage "rollback-after-build-failure" "Rebuilding the PREVIOUS commit ($PREV_COMMIT) also failed after the new commit's build broke. MANUAL INTERVENTION REQUIRED NOW — do not assume the site is up. Check: docker ps, docker compose logs, and $LOG_FILE."
  fi
  exit 1
fi

log "Stage: health-check"
if wait_for_healthy; then
  log "Healthy (login -> $login_code, admin -> $admin_code). Deployed $NEW_COMMIT."
  clear_failure_marker
  exit 0
fi
log "login page -> $login_code, admin page -> $admin_code (gave up after retries)"

fail_stage "health-check" "After deploying $NEW_COMMIT: login page returned $login_code (expected 200), admin page returned $admin_code (expected 200). Containers came up but the app itself isn't responding correctly."

log "Rolling back to $PREV_COMMIT..."
cd "$REPO_DIR" && git checkout "$PREV_COMMIT" >>"$LOG_FILE" 2>&1
cd "$CHAT_DIR" && ./up.sh >>"$LOG_FILE" 2>&1

if wait_for_healthy; then
  log "Rolled back successfully (login -> $login_code, admin -> $admin_code). $NEW_COMMIT did not go live — investigate the health-check failure above before retrying."
else
  fail_stage "rollback-after-health-failure" "Rollback to $PREV_COMMIT completed but login page STILL returns $login_code, not 200, even after retrying. MANUAL INTERVENTION REQUIRED NOW — this is not a code problem, check containers directly: docker ps, docker compose logs."
fi
exit 1
