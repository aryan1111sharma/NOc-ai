#!/usr/bin/env bash
LOG_DIR="/opt/manthan/logs"
if [ -f "$LOG_DIR/last-failure-stage.txt" ]; then
  echo "LAST DEPLOY FAILED"
  echo "Time:  $(cat "$LOG_DIR/last-failure-timestamp.txt" 2>/dev/null)"
  echo "Stage: $(cat "$LOG_DIR/last-failure-stage.txt")"
  echo "Why:"
  cat "$LOG_DIR/last-failure-detail.txt"
else
  echo "No recorded failures. Last deploy (if any) completed and passed its health check."
fi
