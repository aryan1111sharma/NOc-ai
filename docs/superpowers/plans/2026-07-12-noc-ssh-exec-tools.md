# NOC SSH command-execution tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two tools to `mcp-server` — `charger_ssh_prepare` and `charger_ssh_execute` — that run arbitrary shell commands on a live charger over its WebSocket PTY, gated by a mandatory two-step confirmation + audit log.

**Architecture:** New `noc_mcp/ssh_exec.py` holds an in-memory single-use token store, a WebSocket round-trip (`_run_ssh_command`) that wraps the command with an `__QMCP_EXIT_$?__` sentinel to recover clean output + exit code off a raw PTY, and the two `@mcp.tool` wrappers registered via `_register_ssh_tools(mcp, client, ws_url_builder)`. `build_server` gains an optional `ssh_ws_url_builder` param (default derives `wss://` from `noc_base_url`) so tests can point it at an in-process fake WS server. The SSH password is held transiently keyed by the token, used once, never logged.

**Tech Stack:** Python, FastMCP 3.4.4, `websockets` 16 (asyncio client + server for the test fake), pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-12-noc-ssh-exec-tools-design.md`

**Run tests from `mcp-server/`:** `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
(config needs `NOC_EMAIL`/`NOC_PASSWORD` at import; no `.env`; run from the package dir.)

---

## File structure

- **Modify** `noc_mcp/config.py` — add SSH settings.
- **Modify** `requirements.txt` — add `websockets`.
- **Create** `noc_mcp/ssh_exec.py` — token store, `_strip_ansi`/`_parse_output`, `_run_ssh_command`, `_register_ssh_tools`.
- **Modify** `noc_mcp/server.py` — optional `ssh_ws_url_builder` param; call `_register_ssh_tools`.
- **Create** `tests/fake_ssh_ws.py` — in-process fake SSH WebSocket server (scenario-driven).
- **Create** `tests/test_ssh_exec.py` — unit + integration tests.
- **Modify** `README.md` — document the two tools with the safety framing.

---

### Task 1: Config settings + `websockets` dependency

**Files:**
- Modify: `noc_mcp/config.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add settings**

In `noc_mcp/config.py`, add these fields to `Settings` (after `request_timeout`):
```python
    # SSH command-execution tools
    ssh_connect_timeout: float = 15.0
    ssh_command_timeout: float = 45.0
    ssh_max_output_bytes: int = 100_000
    ssh_pending_ttl_seconds: int = 120
```

- [ ] **Step 2: Add the dependency**

Append `websockets>=13` to `requirements.txt` (v16 is already installed in the repo venv).

- [ ] **Step 3: Verify config imports**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -c "from noc_mcp.config import settings; print(settings.ssh_command_timeout, settings.ssh_pending_ttl_seconds)"`
Expected: `45.0 120`

- [ ] **Step 4: Commit**

```bash
git add noc_mcp/config.py requirements.txt
git commit -m "feat(noc): add SSH-exec config settings + websockets dependency"
```

---

### Task 2: Single-use token store

**Files:**
- Create: `noc_mcp/ssh_exec.py`
- Test: `tests/test_ssh_exec.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ssh_exec.py`:
```python
"""Tests for the SSH command-execution tools (noc_mcp.ssh_exec)."""

import pytest

from noc_mcp import ssh_exec


def setup_function():
    ssh_exec._PENDING.clear()


def test_mint_and_pop_roundtrip():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "uptime",
                                  "username": "torizon", "password": "s3cret"})
    assert isinstance(tok, str) and len(tok) >= 16
    req = ssh_exec._pop_pending(tok, ttl=120)
    assert req["command"] == "uptime" and req["password"] == "s3cret"


def test_pop_is_single_use():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "x",
                                  "username": "u", "password": "p"})
    assert ssh_exec._pop_pending(tok, ttl=120) is not None
    assert ssh_exec._pop_pending(tok, ttl=120) is None   # already consumed


def test_pop_rejects_unknown_token():
    assert ssh_exec._pop_pending("nope", ttl=120) is None


def test_pop_rejects_expired_token():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "x",
                                  "username": "u", "password": "p"})
    ssh_exec._PENDING[tok]["created_at"] -= 1000     # force staleness
    assert ssh_exec._pop_pending(tok, ttl=120) is None
    assert tok not in ssh_exec._PENDING              # expired entry is evicted
```

- [ ] **Step 2: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'noc_mcp.ssh_exec'`.

- [ ] **Step 3: Create `ssh_exec.py` with the token store**

Create `noc_mcp/ssh_exec.py`:
```python
"""SSH command-execution tools for a live charger (the one non-read-only capability).

Runs arbitrary shell commands on a charger over its WebSocket PTY
(``wss://{host}/ws/ssh-terminal/{charger_id}``), gated by a mandatory two-step flow:
``charger_ssh_prepare`` mints a single-use confirmation token (holding the request + SSH
password transiently in memory); ``charger_ssh_execute`` consumes the token and runs the
command. The password is never logged, returned, or persisted. See the design spec.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

# token -> {charger_id, command, username, password, created_at}
_PENDING: dict[str, dict] = {}


def _mint_pending(req: dict) -> str:
    """Store a pending SSH request and return its single-use confirmation token."""
    token = secrets.token_urlsafe(24)
    _PENDING[token] = {**req, "created_at": time.time()}
    return token


def _pop_pending(token: str, ttl: int) -> Optional[dict]:
    """Consume a pending request by token. Returns None if unknown, already used, or older
    than ``ttl`` seconds (expired entries are evicted)."""
    req = _PENDING.pop(token, None)
    if req is None:
        return None
    if time.time() - req["created_at"] > ttl:
        return None
    return req
```

- [ ] **Step 4: Run to verify it passes**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -q`
Expected: the 4 token-store tests PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_mcp/ssh_exec.py tests/test_ssh_exec.py
git commit -m "feat(noc): add single-use SSH confirmation-token store"
```

---

### Task 3: ANSI stripping + sentinel output parsing

**Files:**
- Modify: `noc_mcp/ssh_exec.py`
- Test: `tests/test_ssh_exec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ssh_exec.py`:
```python
def test_strip_ansi_removes_escapes():
    assert ssh_exec._strip_ansi("\x1b[32mup\x1b[0m 3 days") == "up 3 days"


def test_parse_output_extracts_exit_code_and_body():
    raw = "up 3 days, load 0.10\r\n__QMCP_EXIT_0__\r\n"
    body, code = ssh_exec._parse_output(raw, command="uptime")
    assert body == "up 3 days, load 0.10"
    assert code == 0


def test_parse_output_nonzero_exit_and_drops_echoed_command():
    raw = "\x1b[0mls: cannot access 'x': No such file or directory\r\n__QMCP_EXIT_2__\r\n"
    body, code = ssh_exec._parse_output(raw, command="ls x")
    assert code == 2
    assert "No such file" in body


def test_parse_output_missing_sentinel_returns_none_code():
    body, code = ssh_exec._parse_output("partial output no marker\n", command="x")
    assert code is None and "partial output" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -k "ansi or parse" -q`
Expected: FAIL — `_strip_ansi`/`_parse_output` not defined.

- [ ] **Step 3: Implement the parsing helpers**

In `ssh_exec.py`, add after the imports (and add `import re`):
```python
import re

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SENTINEL_RE = re.compile(r"__QMCP_EXIT_(\d+)__")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_output(raw: str, command: str) -> tuple[str, Optional[int]]:
    """From accumulated PTY output, strip ANSI, split off the sentinel to get the exit code,
    and drop a leading echoed-command line if the PTY echoed it."""
    clean = _strip_ansi(raw).replace("\r\n", "\n").replace("\r", "\n")
    m = _SENTINEL_RE.search(clean)
    exit_code = int(m.group(1)) if m else None
    body = clean[: m.start()] if m else clean
    lines = body.split("\n")
    if lines and lines[0].strip() == command.strip():   # PTY echoed the typed command
        lines = lines[1:]
    return "\n".join(lines).strip(), exit_code
```

- [ ] **Step 4: Run to verify it passes**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -q`
Expected: all tests so far PASS.

- [ ] **Step 5: Commit**

```bash
git add noc_mcp/ssh_exec.py tests/test_ssh_exec.py
git commit -m "feat(noc): add ANSI stripping + sentinel exit-code parsing for SSH output"
```

---

### Task 4: `_run_ssh_command` + fake SSH WebSocket server

**Files:**
- Modify: `noc_mcp/ssh_exec.py`
- Create: `tests/fake_ssh_ws.py`
- Test: `tests/test_ssh_exec.py`

- [ ] **Step 1: Create the fake SSH WebSocket server**

Create `tests/fake_ssh_ws.py`:
```python
"""In-process fake of the charger SSH WebSocket PTY, for tests.

Scenario is chosen by the last path segment of the ws URL:
  NORMAL -> emits output + __QMCP_EXIT_0__
  FAIL   -> emits output + __QMCP_EXIT_2__
  ANSI   -> emits ANSI-coded output + sentinel
  HUGE   -> emits >max_bytes of output + sentinel
  SLOW   -> never emits the sentinel (client should time out)
  ERR    -> replies {"type":"error"} instead of {"type":"connected"}
"""

import asyncio
import contextlib
import json

from websockets.asyncio.server import serve


async def _handler(ws):
    scenario = ws.request.path.rsplit("/", 1)[-1]
    await ws.recv()  # the connect handshake frame
    if scenario == "ERR":
        await ws.send(json.dumps({"type": "error", "message": "auth failed"}))
        return
    await ws.send(json.dumps({"type": "connected"}))
    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("action") != "data" or "__QMCP_EXIT" not in msg.get("data", ""):
            continue
        if scenario == "SLOW":
            await asyncio.sleep(30)  # client's command_timeout fires first
            continue
        if scenario == "ANSI":
            out = "\x1b[32mroot@charger\x1b[0m:~# \r\nup 3 days\r\n__QMCP_EXIT_0__\r\n"
        elif scenario == "HUGE":
            out = ("x" * 250_000) + "\r\n__QMCP_EXIT_0__\r\n"
        elif scenario == "FAIL":
            out = "boom\r\n__QMCP_EXIT_2__\r\n"
        else:  # NORMAL
            out = "up 3 days, load 0.10\r\n__QMCP_EXIT_0__\r\n"
        await ws.send(json.dumps({"type": "data", "data": out}))
        return


@contextlib.asynccontextmanager
async def fake_ssh_server():
    """Yield the ws:// base URL of a running fake SSH server."""
    async with serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_ssh_exec.py`:
```python
from tests.fake_ssh_ws import fake_ssh_server

TO = dict(connect_timeout=5.0, command_timeout=5.0, max_bytes=100_000)


@pytest.mark.asyncio
async def test_run_ssh_command_normal():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/NORMAL", "tok", "torizon", "p", "uptime", **TO)
    assert res["exit_code"] == 0
    assert res["output"] == "up 3 days, load 0.10"
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_run_ssh_command_nonzero_exit():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/FAIL", "tok", "u", "p", "false", **TO)
    assert res["exit_code"] == 2 and "boom" in res["output"]


@pytest.mark.asyncio
async def test_run_ssh_command_strips_ansi():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/ANSI", "tok", "u", "p", "uptime", **TO)
    assert "\x1b[" not in res["output"] and "up 3 days" in res["output"]


@pytest.mark.asyncio
async def test_run_ssh_command_truncates_huge_output():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/HUGE", "tok", "u", "p", "cat big", **TO)
    assert res["truncated"] is True


@pytest.mark.asyncio
async def test_run_ssh_command_error_frame():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/ERR", "tok", "u", "p", "uptime", **TO)
    assert "error" in res


@pytest.mark.asyncio
async def test_run_ssh_command_times_out():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/SLOW", "tok", "u", "p", "sleep 99",
            connect_timeout=5.0, command_timeout=1.0, max_bytes=100_000)
    assert "error" in res and "timed out" in res["error"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -k run_ssh -q`
Expected: FAIL — `_run_ssh_command` not defined.

- [ ] **Step 4: Implement `_run_ssh_command`**

In `ssh_exec.py`, add (and add the imports `import asyncio`, `import json`, and the websockets imports):
```python
import asyncio
import json

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import WebSocketException


async def _run_ssh_command(
    ws_url: str, token: str, username: str, password: str, command: str,
    *, connect_timeout: float, command_timeout: float, max_bytes: int,
) -> dict:
    """Open the charger SSH PTY, run one command, return {output, exit_code, truncated}.

    Wraps the command with an ``__QMCP_EXIT_$?__`` sentinel to recover clean output + exit
    code from a raw PTY stream. Any failure returns a structured ``{"error": ...}``.
    """
    payload = f"{command}\necho __QMCP_EXIT_$?__\n"
    try:
        async with _ws_connect(ws_url, open_timeout=connect_timeout) as ws:
            await ws.send(json.dumps({
                "action": "connect", "token": token,
                "username": username, "password": password, "cols": 120, "rows": 30,
            }))
            # phase 1: await the 'connected' frame
            deadline = asyncio.get_event_loop().time() + connect_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return {"error": "timed out waiting for SSH connect"}
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if frame.get("type") == "connected":
                    break
                if frame.get("type") == "error":
                    return {"error": f"SSH connect error: {frame.get('message', '')}"}
            # phase 2: send command, accumulate output until the sentinel
            await ws.send(json.dumps({"action": "data", "data": payload}))
            buf: list[str] = []
            total = 0
            truncated = False
            scan_tail = ""
            deadline = asyncio.get_event_loop().time() + command_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return {"error": f"command timed out after {command_timeout}s"}
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                except asyncio.TimeoutError:
                    return {"error": f"command timed out after {command_timeout}s"}
                if frame.get("type") == "error":
                    return {"error": f"SSH error: {frame.get('message', '')}"}
                if frame.get("type") != "data":
                    continue
                d = frame.get("data", "")
                total += len(d)
                if total <= max_bytes:
                    buf.append(d)
                else:
                    truncated = True
                scan_tail = (scan_tail + d)[-256:]     # detect a sentinel spanning frames
                if _SENTINEL_RE.search(scan_tail):
                    break
            output, exit_code = _parse_output("".join(buf), command)
            return {"output": output, "exit_code": exit_code, "truncated": truncated}
    except (WebSocketException, OSError, asyncio.TimeoutError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 5: Run to verify it passes**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/ssh_exec.py tests/fake_ssh_ws.py tests/test_ssh_exec.py
git commit -m "feat(noc): add _run_ssh_command WebSocket round-trip + fake SSH server"
```

---

### Task 5: The two tools + registration + wiring

**Files:**
- Modify: `noc_mcp/ssh_exec.py`
- Modify: `noc_mcp/server.py`
- Test: `tests/test_ssh_exec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ssh_exec.py`:
```python
import httpx
import logging
from fastmcp import Client
from noc_mcp.auth import NocAuthClient
from noc_mcp.server import build_server


def _noc_client():
    """A NocAuthClient whose login returns a token (nothing else is called)."""
    def handler(request):
        return httpx.Response(200, json={"access_token": "noc.tok.jwt", "token_type": "bearer"})
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _server(base):
    return build_server(
        client=_noc_client(),
        ssh_ws_url_builder=lambda cid: f"{base}/ws/ssh-terminal/{cid}",
    )


@pytest.mark.asyncio
async def test_prepare_returns_token_without_connecting():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            res = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
    assert res["charger_id"] == "NORMAL" and res["command"] == "uptime"
    assert isinstance(res["confirmation_token"], str)
    assert "password" not in str(res).lower() or "s3cret" not in str(res)  # secret not echoed
    assert ssh_exec._PENDING  # stored server-side


@pytest.mark.asyncio
async def test_execute_runs_command_end_to_end():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            prep = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
            res = (await c.call_tool("charger_ssh_execute",
                                     {"confirmation_token": prep["confirmation_token"]})).data
    assert res["exit_code"] == 0 and "up 3 days" in res["output"]
    assert "s3cret" not in str(res)                 # password never in output


@pytest.mark.asyncio
async def test_execute_rejects_bad_token_without_connecting():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            res = (await c.call_tool("charger_ssh_execute",
                                     {"confirmation_token": "bogus"})).data
    assert "error" in res


@pytest.mark.asyncio
async def test_execute_audit_logs_command_not_password(caplog):
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            prep = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
            with caplog.at_level(logging.INFO, logger="noc_mcp.ssh_audit"):
                await c.call_tool("charger_ssh_execute",
                                  {"confirmation_token": prep["confirmation_token"]})
    audit = " ".join(r.getMessage() for r in caplog.records)
    assert "uptime" in audit and "NORMAL" in audit
    assert "s3cret" not in audit                    # password never audited
```

- [ ] **Step 2: Run to verify it fails**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest tests/test_ssh_exec.py -k "prepare or execute" -q`
Expected: FAIL — `build_server` has no `ssh_ws_url_builder` param / tools not registered.

- [ ] **Step 3: Add the tools + registrar to `ssh_exec.py`**

In `ssh_exec.py`, add (and `import logging`, `from .config import settings`):
```python
import logging

from .config import settings

_audit = logging.getLogger("noc_mcp.ssh_audit")


def _default_ws_url_builder(charger_id: str) -> str:
    base = settings.noc_base_url.rstrip("/")
    ws = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws}/ws/ssh-terminal/{charger_id}"


def _register_ssh_tools(mcp, client, ws_url_builder=None) -> None:
    """Register the two-step SSH command tools onto ``mcp``."""
    build_ws_url = ws_url_builder or _default_ws_url_builder

    @mcp.tool
    async def charger_ssh_prepare(
        charger_id: str, command: str, ssh_username: str, ssh_password: str,
    ) -> dict:
        """STEP 1 of running a shell command on a LIVE charger over SSH. Does NOT execute —
        it returns a one-time confirmation_token and a preview. You MUST show the user the
        command + charger and get their explicit approval, then call charger_ssh_execute with
        the token. This runs on real hardware; treat it as such."""
        token = _mint_pending({
            "charger_id": charger_id, "command": command,
            "username": ssh_username, "password": ssh_password,
        })
        return {
            "confirmation_token": token,
            "charger_id": charger_id,
            "command": command,
            "expires_in_seconds": settings.ssh_pending_ttl_seconds,
            "warning": ("Runs on a LIVE charger. Show this command to the user and get "
                        "explicit approval before calling charger_ssh_execute."),
        }

    @mcp.tool
    async def charger_ssh_execute(confirmation_token: str) -> dict:
        """STEP 2: run the command from a charger_ssh_prepare the user approved. Requires the
        one-time confirmation_token. Returns {charger_id, command, output, exit_code,
        truncated} or {error}. Read-only servers reject this; this is the one write path."""
        req = _pop_pending(confirmation_token, ttl=settings.ssh_pending_ttl_seconds)
        if req is None:
            return {"error": "Unknown, expired, or already-used confirmation_token. "
                             "Call charger_ssh_prepare again and get user approval."}
        token = await client._ensure_token()
        result = await _run_ssh_command(
            build_ws_url(req["charger_id"]), token, req["username"], req["password"],
            req["command"],
            connect_timeout=settings.ssh_connect_timeout,
            command_timeout=settings.ssh_command_timeout,
            max_bytes=settings.ssh_max_output_bytes,
        )
        _audit.info(
            "ssh_execute charger=%s command=%r exit=%s bytes=%s",
            req["charger_id"], req["command"],
            result.get("exit_code"), len(result.get("output", "")),
        )
        return {"charger_id": req["charger_id"], "command": req["command"], **result}
```

- [ ] **Step 4: Wire into `build_server`**

In `noc_mcp/server.py`, add the import:
```python
from .ssh_exec import _register_ssh_tools
```
Change the `build_server` signature and add the registration call:
```python
def build_server(client: NocAuthClient | None = None, ssh_ws_url_builder=None) -> FastMCP:
    client = client or build_client()
    mcp = FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=client,
        name="QuenchNOC Assistant (read-only)",
        route_maps=_ROUTE_MAPS,
    )
    _register_control_center_catalog(mcp)
    _register_control_center_tools(mcp, client)
    _register_ssh_tools(mcp, client, ssh_ws_url_builder)
    mcp.add_middleware(ReadOnlyProxyMiddleware())
    return mcp
```

- [ ] **Step 5: Run to verify it passes**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
Expected: all tests PASS (auth + readonly + control_center + ssh_exec).

- [ ] **Step 6: Commit**

```bash
git add noc_mcp/ssh_exec.py noc_mcp/server.py tests/test_ssh_exec.py
git commit -m "feat(noc): add charger_ssh_prepare + charger_ssh_execute (two-step, audited)"
```

---

### Task 6: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an SSH section (with safety framing)**

After the "Curated on-charger tools (Tier-1)" section, add:
```markdown
## SSH command execution (the one non-read-only capability)

Two tools run arbitrary shell commands on a **live** charger over its WebSocket PTY, behind a
mandatory two-step guardrail:

1. `charger_ssh_prepare(charger_id, command, ssh_username, ssh_password)` — does **not** execute;
   returns a one-time `confirmation_token` + preview. The assistant must show you the command and
   get explicit approval.
2. `charger_ssh_execute(confirmation_token)` — runs the command (single-use token, ~2 min TTL),
   returns `{output, exit_code, truncated}`, and writes an audit record (`noc_mcp.ssh_audit`).

The SSH password is passed only to `prepare`, held in memory keyed to the token, used once, and
never logged, returned, or persisted. Arbitrary commands are permitted once confirmed (no
denylist). The WebSocket auth uses the server's NOC token, which must have `tools:ssh_terminal`
+ `chargers:control`. Config: `SSH_COMMAND_TIMEOUT`, `SSH_CONNECT_TIMEOUT`,
`SSH_MAX_OUTPUT_BYTES`, `SSH_PENDING_TTL_SECONDS`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(noc): document the two-step SSH command-execution tools"
```

---

### Task 7: Version bump (HELD — do not push)

- [ ] **Step 1: Confirm the full suite is green**

Run: `NOC_EMAIL=x NOC_PASSWORD=x ../.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 2: Do NOT bump/tag/push.** Leave commits on the shared `feat/mcp-server` branch; the
release is HELD until the user coordinates. Report completion + that the SSH tools are the one
non-read-only capability, pending a live smoke test to confirm the service account's
`tools:ssh_terminal` permission.

---

## Self-review notes

- **Spec coverage:** config (§9 → Task 1), token store + TTL/single-use (§6 → Task 2), ANSI/sentinel parse (§4/§7 → Task 3), WS round-trip + timeouts/truncation/error (§7 → Task 4), the two tools + secret handling + audit (§4/§5 → Task 5), permission risk noted in docstring/README (§8 → Tasks 5–6), README safety framing (§11 → Task 6), held release (§12 → Task 7).
- **Secret handling asserted:** `test_prepare_returns_token_without_connecting`, `test_execute_runs_command_end_to_end`, and `test_execute_audit_logs_command_not_password` all assert the password (`s3cret`) never appears in tool output or the audit log.
- **Guardrail asserted:** `test_execute_rejects_bad_token_without_connecting` + the store's single-use/expiry tests (Task 2) prove execute can't run without a fresh, valid token.
- **Type consistency:** `_mint_pending(req)`/`_pop_pending(token, ttl)`, `_parse_output(raw, command) -> (str, int|None)`, `_run_ssh_command(ws_url, token, username, password, command, *, connect_timeout, command_timeout, max_bytes) -> dict`, and `_register_ssh_tools(mcp, client, ws_url_builder=None)` are used identically across tasks; `build_server(client=None, ssh_ws_url_builder=None)`.
- **Live smoke test** (spec §10) is manual (needs a real online charger + the `ssh_terminal` permission) and tracked as a follow-up, not a plan task.
- **No placeholders**; every code step shows complete code.
