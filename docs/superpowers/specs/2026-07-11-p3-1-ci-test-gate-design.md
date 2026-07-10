# P3-1 — CI test gate

**Status:** approved design · **Date:** 2026-07-11 · **Backlog item:** P3-1
**Scope:** new GitHub Actions workflow only. No changes to application code or existing workflows.

## Problem

The repo has test suites for both MCP servers (`mcp-server/tests`, `cms-mcp-server/tests`) but
no CI runs them — only `.github/workflows/changelog.yml` exists (regenerates the changelog on
push to `main`). Nothing prevents a PR from merging with failing tests.

## Goal / acceptance criterion

Every pull request runs both MCP servers' `pytest` suites, and a failing suite makes the PR's
checks fail (so it can be set as a required, merge-blocking check).

## Design

A single new workflow `.github/workflows/tests.yml`.

- **Triggers:** `pull_request` (the gate) and `push` (feedback on direct pushes), with a
  `concurrency` group cancelling superseded runs for the same ref.
- **Job matrix** over `server: [mcp-server, cms-mcp-server]` so each suite runs and reports
  independently (a failure names the offending server). Steps per job:
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5` with `python-version: "3.12"` (matches both Dockerfiles'
     `python:3.12-slim`).
  3. `pip install -r requirements.txt pytest pytest-asyncio` — `pytest`/`pytest-asyncio` are
     test-only deps not present in `requirements.txt` (the READMEs install them ad hoc).
  4. `pytest` run with `working-directory: ${{ matrix.server }}` (each server's `pytest.ini`
     already sets `asyncio_mode = auto`, `testpaths = tests`).
- **Result:** any test failure fails that matrix job → the PR's checks go red.

Both servers' tests run offline with no secrets: `test_auth.py` uses `httpx.MockTransport`, and
`mcp-server`'s `test_readonly.py` sets `NOC_EMAIL`/`NOC_PASSWORD` via `monkeypatch` before a
deferred import, and reads the bundled OpenAPI spec / `rbac-rules.csv` from the checkout.

## Decisions

- **Matrix, not one combined job** — a red check tells you which server broke.
- **Inline test-dep install** (no new `requirements-dev.txt`) — keeps this a single-file change.
- **Python 3.12** to match the container runtime.

## Notes / caveats

- **Merge-blocking is half-setting.** The workflow reports pass/fail; making a red check
  actually block merge requires enabling **branch protection → required status checks** in the
  GitHub repo settings (UI, not committable here). Documented so it isn't forgotten.
- **CI installs `fastmcp>=3.4` fresh** (per `requirements.txt`; local venv is 3.3.1). The first
  CI run therefore also validates the P0-4 middleware tests against the `>=3.4` line; if that
  line moved the middleware API, CI will surface it.

## Out of scope

- Linting, type-checking, coverage thresholds, dependency scanning (P1-7 covers scanning).
- Branch-protection configuration (a GitHub UI setting; noted above).
- Any change to application code or the changelog workflow.

## Verification

- Local: validate the YAML parses, and confirm the exact CI commands pass locally
  (`pip install ... && pytest`) for both servers.
- Remote (authoritative): push the branch and confirm the **tests** workflow runs green in the
  GitHub Actions tab; open a PR to see the checks attached.
