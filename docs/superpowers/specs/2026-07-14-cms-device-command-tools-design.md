# CMS device-command tools (ExecuteApi / ReadJsonLogFile) — design

**Date:** 2026-07-14
**Status:** Proposed
**Author:** kmittal (with Claude)
**Component:** `cms-mcp-server` (Legacy CMS MCP server)

## 1. Problem

The `cms-mcp-server` today is strictly **read-only** (12 tools). The earlier
per-charger work (`2026-07-11-cms-charger-session-tools-design.md`, §2)
**deliberately** wrapped only the device-command *catalog* and *history*, and
explicitly did **not** wrap any execute endpoint.

NOC operators want to **run recovery commands on a live charger** from the same
place they triage it (e.g. "restart the OCPP client on `ab-int64-00033`",
"reset the controller", "read the full state log for that faulted session").
The old NOC does this through three `ChargerDetails` endpoints that we now wrap,
behind a safety model matching the one non-read-only capability already in the
repo — the two-step audited **SSH-exec** gate in `mcp-server`
(`noc_mcp/ssh_exec.py`).

This is a deliberate, scoped reversal of the read-only invariant. The server
stays read-only for **reads**; **writes** are a small curated allow-list behind
a mandatory confirm gate, and the dangerous majority of the device API is
**unreachable in code**.

## 2. Discovery (captured live, 2026-07-14)

Method: read-only calls issued from inside the running `cms-mcp-server`
container, reusing its configured session (credentials never exposed). The
ExecuteApi contract was reverse-engineered from `/src/ChargerSessionLog.js` and
confirmed by scheduling a harmless `id` GET against the **sim** charger
`ab-sam-sim` (ChargerID 1429).

### 2.1 The device-command catalog — `GetAllApisByChargerID` (already wrapped)
Per-charger catalog of **129** device APIs (for `ab-int64-00033`):
`60 GET · 54 POST · 7 PUT · 8 DELETE`, across 18 categories (core device,
scripts, outlets/charging, controllers, RFID, LED, GPIO/PWM, display, meter,
HMI, docker-apps, OCPP client, state logs). Each row:
`ApiID, Name, API, ApiType, ApiTypeID, ApiDescription, CategoryID,
IsInputRequired, IsActive`. **`ApiID` is per-charger** — resolve by charger, do
not hard-code. The full catalog dump lives in
`cms-mcp-server/docs/device-api-catalog.md` (reference artifact, added by this
work).

### 2.2 Execute — `POST /ChargerDetails/ExecuteApi`
- **Body (JSON, note the non-standard MVC wrapper):**
  `'{vvm: ' + JSON.stringify(vvm) + '}'` where
  `vvm = {ChargerID, ApiID, Input, API, Data, UpdatedBy}`.
  - `ChargerID` — numeric charger id.
  - `ApiID` — from the catalog (§2.1).
  - `API` / `Input` — the **resolved** device path (placeholders like
    `{number}`, `{seccid}` substituted). The JS sets both to the same
    "updatedUrl"; `Input` also carries the input value for input-required
    commands in some code paths.
  - `Data` — request body for commands with `IsInputRequired = true` (else omit).
  - `UpdatedBy` — the CMS UserID (from login response, e.g. `74`).
- **Response:** `200`, body is the JSON **string** `"API scheduled
  successfully !"`. **Execution is ASYNCHRONOUS** — this returns a scheduling
  ack, **not** the result, and **not** an ExecutionID.

### 2.3 Execution history — `GetApiExecutionDetailsByID` (already wrapped)
DataTables feed (body per `_datatables_body`, `lvm.ChargerID`). Rows carry the
execution lifecycle: `ExecutionID` (monotonic increasing int), `StatusID`
(`2` = executed; other values TBD-confirm during impl), `Output`
(server-side result-file path, e.g. `E:\ApiResponses\r.txt`), `strExecutedTime`,
`Name`, `API`, `ApiType`.

### 2.4 Read result / log file — `POST /ChargerDetails/ReadJsonLogFile`
- **Body (JSON):** `'{Paths: ' + JSON.stringify(path) + '}'` — a single
  server-side file path string.
- **Returns:** the file **content** (the device's actual JSON response text).
- **Dual use:** reads both command `Output` files **and** the
  `FullStatePath` / `StateLogPath` blobs attached to session/alert rows (the
  "full state log" the existing session/alert tools deliberately trim away).

### 2.5 The end-to-end model
```
ExecuteApi(vvm) ─▶ "API scheduled successfully !"      (async, no ExecutionID)
      │  device round-trip (seconds)
      ▼
GetApiExecutionDetailsByID ─▶ new row {ExecutionID↑, StatusID, Output:"E:\…\r.txt"}
      ▼
ReadJsonLogFile({Paths: Output}) ─▶ device JSON response text
```
**Correlation gotcha:** because ExecuteApi returns no id, a "run-and-return"
tool must correlate its result itself. Strategy: snapshot the **max
`ExecutionID` for this charger before firing**; after firing, poll the history
until a row appears with `ExecutionID > baseline` **and** matching `ApiID`/`API`
and a non-empty `Output`; then `ReadJsonLogFile` it. `ExecutionID` monotonicity
is more robust than timestamps (clock skew). Acceptable race only if the same
charger+command is fired concurrently — out of scope for single-operator NOC use;
we pick the newest matching row.

## 3. Scope

**In scope — 3 new capabilities = 4 MCP tool functions** in a new module
`cms_mcp/device_commands.py` (registered onto the server like
`_register_ssh_tools`; keeps `server.py` from bloating past its ~289 lines):

1. `cms_run_device_query` — read-only device diagnostics (all GET commands),
   resolves the result in one call. *(1 tool)*
2. `cms_run_device_action` — **gated writes** (curated recovery allow-list), a
   two-step `..._prepare` → `..._execute` confirm gate, audited. *(2 tools —
   the prepare/execute pair, per the SSH-exec precedent)*
3. `cms_read_device_log` — read a session/alert state-log blob via
   `ReadJsonLogFile`. *(1 tool)*

**Out of scope (hard-blocked in code — unreachable via any tool):**
`scripts/*` upload/execute/delete (arbitrary code), all `docker-apps/*`,
`hmi` upload, all `gpio`/`pwm` set/export, `controllers/*/network/configureip`
and `stack-selection`, `outlets/{n}/cert`, meter `start`/`stop transaction`,
and every write not on the §5.2 allow-list. Also out of scope: batching,
scheduling, and any non-charger endpoint.

## 4. Allow-lists (the safety boundary)

### 4.1 Read allow-list — `cms_run_device_query`
**Every `ApiType == "GET"` command** (60 of them) is permitted — read-only by
definition. Enforced by looking up the resolved command in the per-charger
catalog and requiring `ApiType == "GET"`.

### 4.2 Write allow-list — `cms_run_device_action` (recommended recovery set)
Exactly these 8 command families, matched by their catalog `API` template:

| API template | Verb | Purpose | Args needed |
|---|---|---|---|
| `reset` | POST | Reset the charger controller | — |
| `controllers/{scripts}/api/outlets/ccs/restartStack` | POST | Reboot the charger (restart stack) | `{scripts}` (secc id) |
| `controllers/{seccid}/api/system/reboot` | POST | Reboot a controller | `{seccid}` |
| `ocpp-client/restart` | POST | Restart OCPP client service | — |
| `ocpp-client/start` | POST | Start OCPP client service | — |
| `ocpp-client/stop` | POST | Stop OCPP client service | — |
| `outlets/{number}/stop` | POST | Stop charge on an outlet | `{number}` (+ body) |
| `services/rfid/clear` | POST | Clear a stuck RFID tag | — |

Anything not on this list → hard refusal, **even with a valid token**. The
allow-list is a module constant; adding to it is a deliberate code change with a
security review.

## 5. Tool designs

### 5.1 `cms_run_device_query(qcharger_id, command, args=None)`
Read-only. Steps:
1. Resolve `qcharger_id → ChargerID` (reuse `_resolve_charger_id`).
2. Load/cache the per-charger catalog; find the row whose `API` matches
   `command` (after placeholder substitution using `args`). Require
   `ApiType == "GET"` (else `ValueError` naming the read allow-list).
3. Substitute `args` into the `API` template; error if any placeholder unfilled.
4. Snapshot baseline max `ExecutionID` for the charger.
5. `ExecuteApi(vvm)` with `Input=API=resolved`, no `Data`.
6. Poll `GetApiExecutionDetailsByID` (bounded, `settings.device_poll_timeout`,
   ~20s; short backoff between polls) for a new row (`ExecutionID > baseline`,
   matching `ApiID`) with non-empty `Output`.
7. `ReadJsonLogFile(Output)`; parse JSON if possible, else return raw text.
8. Return `{qcharger_id, command, execution_id, status_id, executed_at,
   result}`. On poll timeout raise `CmsUnavailableError`-style "result not ready
   / CMS busy, retry" (device may be offline).

### 5.2 `cms_run_device_action` — two-step gate
Mirrors `noc_mcp/ssh_exec.py` exactly (in-memory single-use token store, TTL
sweep, dedicated audit logger). No SSH password — the CMS session cookie is the
credential, so the pending entry holds only the request.

- **`cms_run_device_action_prepare(qcharger_id, command, args=None)`** →
  validates the command is on the §4.2 write allow-list and resolves it against
  the charger's catalog; mints a single-use `confirmation_token`
  (`secrets.token_urlsafe`), stores `{qcharger_id, ChargerID, ApiID, resolved
  API, Data, created_at}`; returns `{confirmation_token, qcharger_id, command,
  resolved_path, expires_in_seconds, warning}`. **Does not execute.** A command
  off the allow-list is refused here (no token minted).
- **`cms_run_device_action_execute(confirmation_token)`** → `_pop_pending`
  (single-use, TTL); re-checks the allow-list defensively; `ExecuteApi(vvm)`;
  **audit-logs** (`cms_mcp.device_audit`: charger, command, resolved path,
  UserID, timestamp); returns the scheduling ack plus, best-effort, the
  correlated `ExecutionID` (same poll as §5.1 but shorter, non-fatal if not
  ready — writes are fire-and-schedule). Unknown/expired/used token → clear
  error telling the agent to re-prepare and get user approval.

Two tools (prepare/execute) rather than one `confirm_token=None` param, matching
the SSH-exec precedent and keeping the destructive call impossible to reach by
accident.

### 5.3 `cms_read_device_log(qcharger_id, path)`
`ReadJsonLogFile({Paths: path})` → returns the blob (JSON-parsed if possible).
**Path guard:** `path` must match the CMS state-log/result path shape
(`^E:\\(ApiResponses|CMS[\\w ]*State_Logs)\\.+\.(txt|json|log)$`, case-insensitive)
— the endpoint appears to read arbitrary server paths, so we constrain it to the
known result/state-log roots to avoid turning it into a file-read primitive.
Intended input is a `FullStatePath`/`StateLogPath`/`Output` value obtained from
the session/alert/history tools; the tool docstring says so.

### 5.4 Shared plumbing
- `_post_vvm(client, path, vvm)` — posts the `{vvm: …}` / `{Paths: …}` wrapper
  bodies with `Content-Type: application/json` + `X-Requested-With` (the wrapper
  is not strict JSON, so build the body string as the JS does).
- Per-charger catalog cache: `command(API template) → {ApiID, ApiType,
  IsInputRequired}`, lazy-loaded via the existing catalog fetch, keyed by
  ChargerID.
- Placeholder substitution + validation helper.
- New settings (in `config.py`): `device_poll_timeout: float = 20.0`,
  `device_poll_interval: float = 2.0`, `device_pending_ttl_seconds: int = 120`,
  `device_audit_log_file: str | None = None`.

## 6. Error handling
- Unknown `qcharger_id` → existing `ValueError` w/ near-match hint.
- Command not in the charger's catalog → `ValueError` listing near matches.
- Read tool given a non-GET command → `ValueError` pointing to
  `cms_run_device_action`.
- Action tool given an off-allow-list command → refusal naming the allow-list.
- CMS flap during execute/poll → `CmsUnavailableError` (existing) surfaced as
  "temporarily unavailable, retry"; note the history DataTables endpoint flaps
  more than most (observed 2026-07-14), so poll must tolerate empty/HTML bodies.
- Device offline / result never appears → bounded-poll timeout with a clear
  "no result within Ns; charger may be offline" message (not an exception for
  the write path — the command *was* scheduled).

## 7. Testing / verification
- **Unit (mocked CMS, extend `tests/fake_cms.py`):**
  - ExecuteApi body shape = `{vvm: …}` with correct fields; ReadJsonLogFile body
    = `{Paths: …}`.
  - Read tool: GET allowed; non-GET rejected; placeholder substitution;
    unfilled placeholder errors; correlation picks the row with
    `ExecutionID > baseline`.
  - Action gate: allow-list membership (each of the 8 in; a Tier-3 like
    `scripts/active` and a plausible-but-off item like `outlets/{n}/e-stop`
    rejected at prepare **and** at execute); token mint/consume single-use;
    expiry; audit line emitted.
  - `cms_read_device_log` path guard: valid `E:\ApiResponses\…` accepted; a
    traversal / arbitrary path (`E:\Windows\…`, `..\..`) rejected.
- **Live smoke (gated on creds + an online charger):** `cms_run_device_query`
  `id`/`version`/`state` returns real device JSON; one gated
  `ocpp-client/restart` end-to-end on a **non-production** / sim charger with
  explicit approval; `cms_read_device_log` on a real session `FullStatePath`.
  Confirm the `StatusID` enum and that `Output` populates.
- **Success criteria:** reads return device output in one call; every
  off-allow-list write is unreachable at both steps; no arbitrary file read via
  the log tool; audit records every executed action; server remains read-only
  for all read paths.

## 8. Security review
Required before release (this adds a write path to field hardware), same bar as
the SSH-exec work. Focus: allow-list completeness (no Tier-3 leakage via
placeholder tricks, e.g. `command="reset"` with `args` injecting extra path
segments), path-guard bypass on `cms_read_device_log`, token unforgeability /
single-use, audit coverage, and that a CMS flap can never cause an unintended
double-execute (ExecuteApi is not idempotent for writes — the poll/retry must
never re-fire `ExecuteApi`, only re-read).

## 9. Docs / release
- Add `cms-mcp-server/docs/device-api-catalog.md` (full 129-command reference).
- Update `cms-mcp-server/README.md` tool table + a "device commands (gated)"
  section explaining the read/write boundary and the allow-list.
- Version bump per team convention (`scripts/bump-version.ps1`, `feat` → minor)
  on completion — but **coordinate the release** (branch is shared; see the
  held-release note in project memory) and land the **security review** first.
