# CI/CD real-repo test log

This file exists only to safely trigger and verify the automated deploy
pipeline on the real production repo for the first time. It is not part
of any Docker build context, so this change should trigger zero container
rebuilds - only noc-librechat restarts briefly (up.sh always does this
at the end of every deploy to re-handshake with MCP servers).

Test run: 2026-09-21
Tests-gate verification run: 2026-09-21 - confirming deploy.yml now waits for the Tests workflow to succeed before deploying.
