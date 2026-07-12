# NOC SSH command-execution tools — design

**Date:** 2026-07-12
**Status:** Proposed
**Author:** kmittal (with Claude)
**Component:** `mcp-server` (`noc_mcp`)

> ⚠️ **This is the only non-read-only capability in the project.** It runs arbitrary shell
> commands on live production EV chargers. The design is built around a mandatory two-step
> human-in-the-loop guardrail + full audit. Read §5–§6 carefully.

## 1. Problem

The NOC ships a browser SSH terminal (`/tools/ssh_tool.html`) that opens an **interactive PTY**
to a charger over a WebSocket. Operators want the assistant to run diagnostic/admin commands on a
charger's shell. There is no REST "exec" endpoint — the only mechanism is the WebSocket PTY.

## 2. Scope

**In scope:** two tools — `charger_ssh_prepare` + `charger_ssh_execute` — wrapping the WS PTY,
with a two-step confirmation guardrail and an audit log. Arbitrary commands are allowed (the user
chose "confirm + audit", no denylist).

**Out of scope:** SFTP file transfer (the web tool supports it; we do not wrap it); a command
denylist/allowlist; any autonomous/unconfirmed execution path.

## 3. Mechanism (captured live from `ssh_tool.html`, 2026-07-12)

- **WebSocket:** `wss://{host}/ws/ssh-terminal/{charger_id}` (host from `noc_base_url`).
- **Handshake:** on open, send
  `{"action":"connect","token":<NOC access token>,"username":<ssh user>,"password":<ssh pass>,"cols":120,"rows":30}`.
- **Server → client** JSON frames: `{"type":"connected"}` (session up), `{"type":"data","data":<terminal output>}` (raw PTY stream, ANSI included), `{"type":"error",...}`.
- **Client → server** input: `{"action":"data","data":<keystrokes>}`.
- The read-only `GET /api/v1/chargers/{charger_id}/ssh-status` returns reachability + proxy
  details (`proxy_port`, `proxy_private_ip`, `ssh_jump_command`) and is used as a precheck.

## 4. Tools (new module `noc_mcp/ssh_exec.py`)

### `charger_ssh_prepare(charger_id, command, ssh_username, ssh_password) -> dict`
- Does **not** execute anything.
- Optionally calls `ssh-status` to report whether SSH is reachable.
- Stores the pending request `{charger_id, command, ssh_username, ssh_password, created_at}`
  **in-memory** under a random **single-use `confirmation_token`** with a TTL (default 120 s).
- Returns a **preview** the model must show the user:
  `{confirmation_token, charger_id, command, ssh_reachable, expires_in_seconds, warning}`
  where `warning` states this will run on a live charger and needs approval.

### `charger_ssh_execute(confirmation_token) -> dict`
- Pops the pending request (single-use). Unknown / expired / already-used token → `{"error": ...}`.
- Opens the WebSocket, sends the `connect` handshake (NOC service-account token + the stored SSH
  creds), and waits for `{"type":"connected"}` (connect timeout, default 15 s).
- Sends the command wrapped with a unique sentinel so a raw PTY yields clean boundaries + exit code:
  `"{command}\n"` followed by `"echo __QMCP_EXIT_$?__\n"` (sentinel `__QMCP_EXIT_<code>__`).
- Accumulates `{"type":"data"}` frames until the sentinel appears (or command timeout, default 45 s);
  **strips ANSI escape sequences**, removes the echoed command + sentinel lines, extracts the exit code.
- Enforces a **max output size** (default 100 000 bytes; truncate + set `truncated: true`).
- Writes an **audit record** (see §5), then returns
  `{charger_id, command, output, exit_code, truncated}` (or `{error}` on failure / `{type:"error"}` frame).

## 5. Secret handling (mandatory)

- The **SSH password** is accepted only by `prepare`, held transiently in the in-memory pending map
  keyed by the token, used exactly once by `execute`, and discarded on execute or TTL expiry.
- The password is **never** logged, **never** returned in any tool output, **never** written to disk,
  **never** committed.
- The **audit log** records `timestamp, charger_id, command, exit_code, output_bytes` — and
  **not** the password and **not** the full output (output size only). Audit goes to the `noc_mcp`
  logger (and/or a dedicated audit file); it is append-only intent.

## 6. Guardrail — the two-step is the safety mechanism

- `execute` cannot run without a fresh, unused, unexpired token minted by a `prepare` call the user
  approved. This keeps a human in the loop on the irreversible action.
- Token = `secrets.token_urlsafe(...)`, single-use (popped on execute), TTL default 120 s.
- No denylist (per the user's "confirm + audit" choice) — arbitrary commands are permitted once
  confirmed. (A denylist could be layered in later without changing the tool surface.)
- The tools' docstrings must state plainly: this runs on a live charger, `execute` requires a
  `prepare` the user has approved, and the model must present the `prepare` preview and get explicit
  user confirmation before calling `execute`.

## 7. WebSocket client + parsing

- Add the **`websockets`** dependency (httpx cannot do WebSocket).
- `_run_ssh_command(ws_url, connect_msg, command, *, connect_timeout, command_timeout, max_bytes)` —
  the low-level round-trip, written so tests can point it at an in-process fake WS server (or inject a
  connector). Returns `{output, exit_code, truncated}` or a structured error dict.
- ANSI strip: regex `\x1B\[[0-?]*[ -/]*[@-~]` (CSI) plus common OSC/simple escapes.
- Handle: `{"type":"error"}` frame, WS close before sentinel, connect timeout, command timeout.

## 8. Auth / permission risk

The WS `connect.token` uses the **mcp-server's NOC service-account token** (from `NocAuthClient`).
That account must hold **`tools:ssh_terminal`** and **`chargers:control`** permissions or the WS
will reject the session. The current service account is read-only and likely **lacks** these — the
live smoke test (§10) confirms; if it fails, either elevate the account or (future) accept a NOC
token per call. This is surfaced in the tool's error output as `{error}`, not a crash.

## 9. Config (`noc_mcp/config.py`)

- `ssh_command_timeout: float = 45.0`
- `ssh_connect_timeout: float = 15.0`
- `ssh_max_output_bytes: int = 100_000`
- `ssh_pending_ttl_seconds: int = 120`
- WS base URL derived from `noc_base_url` (`https`→`wss`, `http`→`ws`).

## 10. Testing

- **`tests/fake_ssh_ws.py`** — an in-process `websockets` server emulating the charger PTY: accepts
  the `connect` handshake, replies `{"type":"connected"}`, echoes input, and emits the sentinel line
  with a chosen exit code (plus variants: `{"type":"error"}`, slow/no sentinel for timeout, huge
  output for truncation, ANSI-laden output for stripping).
- **`tests/test_ssh_exec.py`** — via the FastMCP in-memory `Client`:
  - `prepare` returns a token + preview and does **not** connect;
  - `execute(token)` runs the command, parses the exit code, strips ANSI, returns clean output;
  - large output → `truncated: true`; command timeout → `{error}`; `{"type":"error"}` → `{error}`;
  - token **reuse** and **expiry** are rejected;
  - password never appears in `execute` output or the audit record.
- **Live smoke test (manual):** against `qflex-neo-240`, run a harmless command (`uptime`), confirm
  the WS connect works with the service-account token (or reveals the §8 permission gap).

## 11. File structure

- **Create** `noc_mcp/ssh_exec.py` (~200–300 lines: pending-store, `_run_ssh_command`, the two tools,
  `_register_ssh_tools`).
- **Modify** `noc_mcp/server.py`: call `_register_ssh_tools(mcp, client)` in `build_server`.
- **Modify** `noc_mcp/config.py`: add the §9 settings.
- **Modify** `requirements.txt`: add `websockets`.
- **Create** `tests/fake_ssh_ws.py`, `tests/test_ssh_exec.py`.
- **Modify** `README.md`: document the two tools with the safety framing (mark clearly as the one
  non-read-only capability + the required approval flow).

## 12. Release

HELD, same as the other feature branches — no bump/tag/push until the user coordinates the shared
`feat/mcp-server` branch.
