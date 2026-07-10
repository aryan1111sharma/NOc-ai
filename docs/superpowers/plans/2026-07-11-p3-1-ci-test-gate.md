# P3-1 CI Test Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A GitHub Actions workflow that runs both MCP servers' `pytest` suites on every PR (and push), failing the checks on any test failure.

**Architecture:** One declarative workflow `.github/workflows/tests.yml` with a job matrix over the two server directories. No application-code changes.

**Tech Stack:** GitHub Actions (`actions/checkout@v4`, `actions/setup-python@v5`), Python 3.12, pytest / pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-11-p3-1-ci-test-gate-design.md`

**Note:** a declarative CI file has no runtime "failing test" to write first, so verification is: (a) YAML parses, (b) the exact CI commands pass locally for both servers, (c) the workflow runs green on GitHub after push (authoritative).

---

### Task 1: Add the tests workflow

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Tests

# Run both MCP servers' pytest suites on PRs (the merge gate) and on pushes.
on:
  push:
  pull_request:

# Latest run per ref wins; cancel superseded runs.
concurrency:
  group: tests-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  pytest:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        server: [mcp-server, cms-mcp-server]
    name: pytest (${{ matrix.server }})
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        working-directory: ${{ matrix.server }}
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt pytest pytest-asyncio

      - name: Run tests
        working-directory: ${{ matrix.server }}
        run: pytest -v
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 3: Reproduce the CI steps locally for BOTH servers**

Run (mcp-server):
`cd mcp-server && ../.venv/Scripts/python -m pytest -q`
Expected: all pass (21 passed).

Run (cms-mcp-server):
`cd cms-mcp-server && ../.venv/Scripts/python -m pytest -q`
Expected: all pass.

(These mirror what CI will do; if a suite fails locally it will fail in CI.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: run both MCP servers' pytest suites on PR and push"
```

---

### Task 2: Close out P3-1

**Files:**
- Modify: `docs/PRODUCTION-READINESS.md`

- [ ] **Step 1: Mark P3-1 done in the backlog**

Change the P3-1 checkbox from `- [ ]` to `- [x]` in `docs/PRODUCTION-READINESS.md`.

- [ ] **Step 2: Commit**

```bash
git add docs/PRODUCTION-READINESS.md
git commit -m "chore: mark P3-1 (CI test gate) done"
```

- [ ] **Step 3: Follow-up (manual, not committable)**

After pushing, in GitHub repo settings enable **branch protection → require status checks →
`pytest (mcp-server)` and `pytest (cms-mcp-server)`** so red checks block merge. Record this as
done when the setting is applied.

---

## Self-Review

**Spec coverage:**
- Both suites on every PR → matrix job on `pull_request`. ✓
- Failing suite fails checks → `pytest` non-zero exit fails the job; `fail-fast: false` so both report. ✓
- Python 3.12 matches Dockerfiles. ✓
- Test-only deps installed inline. ✓
- `working-directory` per server picks up each `pytest.ini`. ✓
- Merge-blocking = branch protection → captured as Task 2 Step 3 (manual). ✓

**Placeholder scan:** none — full YAML and exact commands provided.

**Consistency:** matrix values `mcp-server`/`cms-mcp-server` match the real directory names and the `working-directory` usage; job name references `matrix.server`.
