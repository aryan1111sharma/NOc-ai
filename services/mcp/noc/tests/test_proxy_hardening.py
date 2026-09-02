"""Security regression tests for charger-proxy URL construction (noc_mcp.control_center).

Separate from test_control_center.py (which covers behaviour) because these assert a security
invariant with its own fixture shape: a transport that *captures* outgoing requests so we can
assert on what actually goes out, not on the return value.

THE ORACLE DECODES. An earlier version of this file asserted against ``request.url.raw_path``,
which is still percent-encoded -- so ``"?" not in raw`` and ``_WRITE_PATH not in raw`` passed
trivially, because the ``/`` were ``%2F`` and the ``?`` was ``%3F``. That measured the
client-side wire, which is precisely the layer that does NOT reflect what a decoding receiver
sees, and it passed over a live arbitrary-port / arbitrary-path GET SSRF. Every assertion here
now decodes first. tests/test_receiver_model.py takes the stronger route and asserts on a real
router's resolved path_params.

The invariant: no model-controlled string reaching ``_proxy_get`` may alter the *structure* of
the outgoing URL as a decoding receiver parses it -- it may not add a query parameter, may not
escape its path segment, and may not choose the port or the on-charger path.
"""

import asyncio
from urllib.parse import unquote

import httpx
import pytest
from fastmcp import Client
from fastmcp.tools import FunctionTool

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import _proxy_get
from noc_mcp.reports import REPORTS, STATS
from noc_mcp.server import _EXCLUDED_PREFIXES, build_server
from tests.fake_noc import build_test_server

_LOGIN = "/api/v1/auth/login"

# A real non-GET on-charger endpoint, from noc_mcp/catalog/rbac-rules.csv line 122:
#   8003,POST,/api/v1/pin/contactor/dual,admin,qflex-charging,Dual Contactor Control
_WRITE_PORT = 8003
_WRITE_PATH = "api/v1/pin/contactor/dual"

# Confirmed attack payloads.
_EVIL_CHARGER_ID = f"CHG-1/proxy/{_WRITE_PORT}/{_WRITE_PATH}?method=POST&x="
# The port+path hijack: survives encoding because the receiver decodes before routing.
_EVIL_CHARGER_ID_HIJACK = "CHG-1/proxy/8005/api/v1/devtools/logs/x"
_EVIL_PATH_TRAVERSAL = f"api/v1/devtools/logs/../../../../{_WRITE_PATH}?method=POST"


def _capturing():
    """Return ``(seen, handler)``: handler answers login, records every other request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return seen, handler


def _client(handler):
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _assert_safe(request: httpx.Request) -> None:
    """Assert an outgoing request is safe *as a decoding receiver would parse it*."""
    assert request.method == "GET"
    assert "method" not in request.url.params, f"method query smuggled: {request.url}"

    # Decode exactly like the receiving ASGI server does before it routes.
    decoded = unquote(request.url.raw_path.decode())

    assert "?" not in decoded, f"query separator survives decoding: {decoded}"
    assert "#" not in decoded, f"fragment separator survives decoding: {decoded}"
    assert _WRITE_PATH not in decoded, f"reached a write endpoint: {decoded}"
    # The route template splits on '/proxy/'; a second one lets the caller re-split the route
    # and choose the port + on-charger path.
    assert decoded.count("/proxy/") == 1, f"route re-split possible: {decoded}"

    head, _, _tail = decoded.partition("/proxy/")
    assert head.startswith("/api/v1/chargers/"), decoded
    segment = head[len("/api/v1/chargers/"):]
    assert "/" not in segment, f"charger_id escaped its segment: {segment!r}"
    assert segment not in (".", ".."), f"charger_id is a dot segment: {segment!r}"
    assert ".." not in decoded.split("/"), f"dot segment survives decoding: {decoded}"


def _assert_rejected(seen, out, port=None, path=None):
    """No request left the process, and the caller got the structured error shape."""
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert "error" in out
    if port is not None:
        assert out["port"] == port
    if path is not None:
        assert out["path"] == path


# --------------------------------------------------------------------------------------
# charger_id -- validated, not merely encoded
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("charger_id", [
    _EVIL_CHARGER_ID,
    _EVIL_CHARGER_ID_HIJACK,
    "CHG-1/proxy/8003/api/v1/pin/contactor/status?x=",
    "CHG-1?method=POST",
    "CHG-1#frag",
    "CHG-1&a=b",
    "CHG-1/x",
    "a%2Fb",
    "a b",
    "a:b",
    "a~b",
    "a\\b",
    ".",
    "..",
    "../..",
    "a/..",
    "x/../../y",
    "..%2f",
    "CHG-1\n",     # Python's '$' matches before a trailing newline; the anchor must be \\Z
    "..\n",        # ... and this one would also have slipped past the dot-segment check
    "",
    "   ",
    "\t\n",
])
@pytest.mark.asyncio
async def test_unsafe_charger_ids_are_rejected_without_a_request(charger_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    _assert_rejected(seen, out, port=8003, path="api/v1/charging/status")


@pytest.mark.parametrize("charger_id", [
    "CHG-1", "QC_0012", "abc.def-9", "60f1a2b3c4d5e6f708192a3b", "OFFLINE", "..a", "a..",
])
@pytest.mark.asyncio
async def test_real_charger_ids_still_pass_unchanged(charger_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, charger_id, 8003, "api/v1/charging/status")
    assert out == {"ok": True}
    assert len(seen) == 1
    _assert_safe(seen[0])
    assert str(seen[0].url) == (
        f"http://noc.test/api/v1/chargers/{charger_id}/proxy/8003/api/v1/charging/status")


# --------------------------------------------------------------------------------------
# port
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port", ["8003/api/v1/pin/contactor/dual?method=POST", "80x", None,
                                  "..", 0, 70000, True])
@pytest.mark.asyncio
async def test_non_numeric_or_out_of_range_port_is_rejected(port):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, "api/v1/charging/status")
    _assert_rejected(seen, out)


@pytest.mark.asyncio
async def test_numeric_string_port_is_coerced_and_works():
    seen, handler = _capturing()
    async with _client(handler) as c:
        await _proxy_get(c, "CHG-1", "8003", "api/v1/charging/status")
    assert len(seen) == 1
    assert seen[0].url.raw_path.decode() == (
        "/api/v1/chargers/CHG-1/proxy/8003/api/v1/charging/status")


# --------------------------------------------------------------------------------------
# path
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    _EVIL_PATH_TRAVERSAL,
    "api/v1/devtools/logs/../../pin/contactor/dual",
    "../pin/contactor/dual",
    "api/./v1/charging/status",
    "api/v1/charging/status/..",
])
@pytest.mark.asyncio
async def test_dot_segment_paths_are_rejected_without_a_request(path):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, path)
    _assert_rejected(seen, out, port=8005, path=path)


@pytest.mark.parametrize("path", [
    "api/v1/charging/status?method=POST",
    "api/v1/charging/status#frag",
    "api/v1/charging\\status",
    "api/v1/charging/status?x=1",
    "api/v1/devtools/logs/app%2F..%2F..%2Fpin",
    "api/v1/ charging/status",
    "",
    "   ",
    "/api/v1/charging/status",   # leading slash -> empty segment; callers pass none
    "api//v1/charging/status",
])
@pytest.mark.asyncio
async def test_structurally_unsafe_paths_are_rejected_without_a_request(path):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8003, path)
    _assert_rejected(seen, out, port=8003, path=path)


@pytest.mark.parametrize("source", [
    "app", "ocpp", "syslog", "kern.log", "app-2026-09-01.log",
    # service/unit-style names the widened allowlist deliberately admits
    "qflex-charging.service", "qflex@1.service", "app:main", "a+b",
])
@pytest.mark.asyncio
async def test_plausible_log_source_names_are_not_rejected(source):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", 8005, f"api/v1/devtools/logs/{source}")
    assert out == {"ok": True}, f"log source {source!r} was rejected"
    assert len(seen) == 1
    _assert_safe(seen[0])


# --------------------------------------------------------------------------------------
# legitimate behaviour is byte-identical to before the hardening
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("port,path", [
    (8003, "api/v1/charging/status"),
    (8003, "api/v1/charging/status/1"),
    (8003, "api/v1/session_details/active/1"),
    (8003, "api/v1/session_details/history"),
    (8003, "api/v1/devices/meters"),
    (8003, "api/v1/devices/imd/1"),
    (8003, "api/v1/ocpp/status"),
    (8003, "api/v1/config/ocpp"),
    (8000, "api/v1/network/status"),
    (8000, "api/v1/network/gsm/status"),
    (8000, "api/v1/system/hardware"),
    (8000, "api/v1/system/software"),
    (8000, "api/v1/system/rtc"),
    (8002, "api/state"),
    (8002, "api/pm_count"),
    (8002, "api/pm/get_all_power"),
    (8006, "error-generator/alarms/latched"),
    (8006, "error-generator/errors/pending"),
    (8006, "error-generator/errors/by-gun/1"),
    (8006, "temp/sensors"),
    (8006, "temp/thresholds"),
    (8005, "api/v1/devtools/logs/sources"),
    (8005, "api/v1/devtools/logs/app"),
    (8005, "api/v1/devtools/metrics"),
])
@pytest.mark.asyncio
async def test_every_curated_url_is_unchanged(port, path):
    """Covers every (port, path) the 13 curated tools can emit -- byte-identical URLs."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _proxy_get(c, "CHG-1", port, path)
    assert out == {"ok": True}
    assert len(seen) == 1
    assert str(seen[0].url) == f"http://noc.test/api/v1/chargers/CHG-1/proxy/{port}/{path}"


# --------------------------------------------------------------------------------------
# end-to-end: no curated tool can be steered off its intended endpoint
# --------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------
# end-to-end: DERIVED coverage -- every registered tool that is handed an identifier
# --------------------------------------------------------------------------------------
#
# This section used to be ``_CURATED_CALLS``: a hand-written list of 17 calls, all of them
# pre-existing ``charger_get_*``. Not one ``noc_*`` tool was on it, and that omission is
# exactly why an unvalidated ``charger_id`` shipped in noc_mcp/history.py -- the five history
# composites were never handed a hostile id by anything in this suite. A list that has to be
# remembered is not a control; it is a habit with a test around it. ``readonly._reaches_charger``
# rejects the same anti-pattern in its own docstring, for the same reason.
#
# The targets below are DERIVED from the server's own registered tools: anything whose input
# schema declares ``charger_id`` or ``session_id`` is, by definition, told which charger or
# session to reach, and gets the hostile-input treatment automatically. A tool added tomorrow
# is covered the moment it is registered, with no registry to keep in sync.
#
# One documented limit to that claim, so it is not read as stronger than it is: a tool that
# takes its charger id in a SEPARATE, EARLIER call is not derivable from its own schema.
# ``charger_ssh_execute`` is the only such case today -- it accepts a ``confirmation_token``
# and nothing else, because ``charger_ssh_prepare`` already validated the id and parked it in
# server-side state. ``charger_ssh_prepare`` IS a derived target, but it opens a WebSocket
# rather than an HTTP request, so the wire assertions below are vacuous for it; its own
# rejection is covered in tests/test_ssh_exec.py, and ``_default_ws_url_builder``
# re-validates. Derivation covers "told which charger *in this call*"; a multi-step flow
# needs its own test, and has one.
#
# Two tiers, split by who builds the outgoing URL -- also a structural property, not a name:
#
#   * ``FunctionTool``  -- registered by this repo, so THIS repo builds the URL. A hostile
#     identifier must either produce no request at all, or ride along as an opaque query-string
#     VALUE (``noc_report``/``noc_stats`` take charger_id as a filter, where it cannot alter
#     routing). Anything else means it was spliced into the path.
#   * ``OpenAPITool``   -- generated by FastMCP from the bundled spec, which builds those URLs
#     itself. They are held to the verb-override invariant only; see
#     ``test_generated_openapi_tools_should_reject_a_hostile_charger_id`` in
#     tests/test_receiver_model.py for the pre-existing gap that leaves, which this branch
#     neither introduced nor closes.

_ID_ARGS = ("charger_id", "session_id")

# Hostile identifiers. The first two are the confirmed charger-proxy payloads; the third is the
# confirmed history payload, which needs no '?' at all -- it simply climbs out of the
# /api/v1/chargers/ prefix into a module the route maps deliberately exclude.
_EVIL_TRAVERSAL = "../../../api/v1/admin/users"
_HOSTILE_IDS = (_EVIL_CHARGER_ID, _EVIL_CHARGER_ID_HIJACK, _EVIL_TRAVERSAL)

# Modules the route maps exclude outright; a hostile id must not reach them by traversal.
_FORBIDDEN_PREFIXES = tuple(pattern.lstrip("^") for pattern in _EXCLUDED_PREFIXES)

# Benign values for the *other* required arguments, so each tool actually runs. Taken from the
# modules' own enums where one exists -- a wrong filler only costs coverage, never safety.
_FILLERS = {
    "report": sorted(REPORTS)[0],
    "metric": sorted(STATS)[0],
    "source": "app",
    "command": "uptime",
    "ssh_username": "svc",
    "ssh_password": "pw",
    "port": 8003,
    "path": "api/v1/charging/status",
}


def _filler_args(schema: dict) -> dict:
    """Benign values for every required argument that is not an identifier under test."""
    props = (schema or {}).get("properties") or {}
    args = {}
    for name in (schema or {}).get("required") or []:
        if name in _ID_ARGS:
            continue
        if name in _FILLERS:
            args[name] = _FILLERS[name]
            continue
        kind = (props.get(name) or {}).get("type")
        args[name] = {"integer": 1, "number": 1, "boolean": False,
                      "array": [], "object": {}}.get(kind, "x")
    return args


def _derive_identifier_targets():
    """Every registered tool that accepts an identifier, plus how to call it.

    Returns ``[(tool_name, repo_built, id_args, filler_args), ...]``.
    """
    async def _load():
        return await build_test_server().list_tools()

    targets = []
    for tool in asyncio.run(_load()):
        schema = tool.parameters or {}
        ids = tuple(n for n in _ID_ARGS if n in ((schema.get("properties") or {})))
        if not ids:
            continue
        targets.append((tool.name, isinstance(tool, FunctionTool), ids, _filler_args(schema)))
    return targets


_ID_TARGETS = _derive_identifier_targets()
_ID_TARGET_IDS = [name if repo else f"{name}[generated]"
                  for name, repo, _ids, _args in _ID_TARGETS]


def _decoded_path(request: httpx.Request) -> str:
    """The path a decoding receiver would route on.

    ``raw_path`` carries the query string too, so the split happens BEFORE decoding: only a
    real, unencoded '?' separates a query on the wire. Decoding first would let an encoded
    ``%3F`` masquerade as a query separator and hide the rest of the path from every assertion.
    """
    raw_path, _, _query = request.url.raw_path.decode().partition("?")
    return unquote(raw_path)


def _assert_no_verb_override(request: httpx.Request, tool_name: str) -> None:
    """Universal: no identifier may become the ``method`` the backend forwards to a charger."""
    assert request.method == "GET", f"{tool_name} issued {request.method}"
    assert "method" not in request.url.params, (
        f"{tool_name} smuggled a verb override: {dict(request.url.params)}")


@pytest.mark.asyncio
async def test_a_hostile_session_id_is_exercised_with_a_VALID_charger_id():
    """Otherwise charger_id short-circuits first and the session_id branch is never reached.

    This is the end-to-end companion to the unit coverage in
    tests/test_backend_url_hardening.py: a legal charger with a hostile session id must still
    cost zero outbound requests.
    """
    seen, handler = _capturing()
    async with Client(build_server(_client(handler))) as mcp:
        for evil in (*_HOSTILE_IDS, "S1/../../../api/v1/admin/users", "S1?method=POST"):
            seen.clear()
            await mcp.call_tool("noc_session_events",
                                {"charger_id": "CHG-1", "session_id": evil})
            assert seen == [], (
                f"noc_session_events sent a request for session_id={evil!r}: "
                f"{[str(r.url) for r in seen]}")


def test_identifier_targets_are_derived_and_cover_the_curated_tools():
    """The derivation must be non-degenerate, and must include what the old list missed."""
    names = {name for name, _repo, _ids, _args in _ID_TARGETS}
    missed_by_the_old_hardcoded_list = {
        "noc_charger_history", "noc_charger_telemetry", "noc_session_events",
        "noc_charger_stability", "noc_charger_ocpp_config",
    }
    assert missed_by_the_old_hardcoded_list <= names, (
        f"derivation missed the history composites: {missed_by_the_old_hardcoded_list - names}")
    assert {"charger_get_charging_status", "charger_get_logs", "charger_ssh_prepare"} <= names
    assert len(_ID_TARGETS) >= 30, f"derivation collapsed to {len(_ID_TARGETS)} targets"
    assert any(n == "noc_session_events" and "session_id" in ids
               for n, _repo, ids, _args in _ID_TARGETS)


# Benign stand-ins, so exactly ONE identifier is hostile per call. Setting every id arg at
# once let ``noc_session_events`` short-circuit on charger_id and never exercise its
# session_id branch at all.
_BENIGN_IDS = {"charger_id": "CHG-1", "session_id": "S1"}


def _hostile_variants(id_args, filler):
    """One ``(hostile_arg, evil, args)`` per (identifier, payload) pair."""
    for name in id_args:
        for evil in _HOSTILE_IDS:
            args = {**filler,
                    **{other: _BENIGN_IDS[other] for other in id_args if other != name},
                    name: evil}
            yield name, evil, args


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,repo_built,id_args,filler", _ID_TARGETS,
                         ids=_ID_TARGET_IDS)
async def test_no_tool_lets_a_hostile_identifier_reach_the_wire(
        tool_name, repo_built, id_args, filler):
    seen, handler = _capturing()
    async with Client(build_server(_client(handler))) as mcp:
        for hostile_arg, evil, args in _hostile_variants(id_args, filler):
            seen.clear()
            try:
                await mcp.call_tool(tool_name, args)
            except Exception:  # noqa: BLE001 - a refusal is a pass; only the wire is judged
                pass
            for request in seen:
                _assert_no_verb_override(request, tool_name)
                if not repo_built:
                    continue
                path = _decoded_path(request)
                carried_as_a_filter = (
                    any(value == evil for value in request.url.params.values())
                    # The exemption is per VALUE, not per request: a tool that both spliced
                    # the id into the path AND echoed it as a filter must not be waved
                    # through, so the path is cleared first.
                    and evil not in path
                    and not any(part and part in path
                                for part in (_WRITE_PATH, "/proxy/", ".."))
                )
                if carried_as_a_filter:
                    continue
                pytest.fail(
                    f"{tool_name} spliced a rejected {hostile_arg} into the URL path "
                    f"for {evil!r}: {path} params={dict(request.url.params)}")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,repo_built,id_args,filler", _ID_TARGETS,
                         ids=_ID_TARGET_IDS)
async def test_a_hostile_identifier_never_reaches_an_excluded_module_or_a_write_endpoint(
        tool_name, repo_built, id_args, filler):
    """Traversal check, on the decoded wire, for the tools whose URLs this repo builds."""
    if not repo_built:
        pytest.skip("URL built by FastMCP's OpenAPI layer, not by this repo")
    seen, handler = _capturing()
    async with Client(build_server(_client(handler))) as mcp:
        for _hostile_arg, _evil, args in _hostile_variants(id_args, filler):
            seen.clear()
            try:
                await mcp.call_tool(tool_name, args)
            except Exception:  # noqa: BLE001
                pass
            for request in seen:
                path = _decoded_path(request)
                assert path.startswith("/api/v1/"), f"{tool_name} escaped the API root: {path}"
                assert ".." not in path.split("/"), f"{tool_name} dot segment: {path}"
                assert _WRITE_PATH not in path, f"{tool_name} reached a write endpoint: {path}"
                for prefix in _FORBIDDEN_PREFIXES:
                    assert not path.startswith(prefix), (
                        f"{tool_name} reached excluded module {prefix}: {path}")


@pytest.mark.asyncio
async def test_charger_get_logs_source_cannot_traverse_to_a_write_endpoint():
    seen, handler = _capturing()
    evil_sources = [
        "../../../../" + _WRITE_PATH + "?method=POST",
        "..%2F..%2Fpin%2Fcontactor%2Fdual",
        "app?method=POST",
        "app/../../../pin/contactor/dual",
        "app#x",
    ]
    async with Client(build_server(_client(handler))) as mcp:
        for source in evil_sources:
            await mcp.call_tool(
                "charger_get_logs", {"charger_id": "CHG-1", "source": source})
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"


@pytest.mark.asyncio
async def test_direct_proxy_get_cannot_reach_the_write_endpoint():
    """Every confirmed reproduction payload, asserted on the decoded view."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        for cid, port, path in [
            (_EVIL_CHARGER_ID, 8003, "api/v1/charging/status"),
            (_EVIL_CHARGER_ID_HIJACK, 8003, "api/v1/charging/status"),
            ("CHG-1", 8005, _EVIL_PATH_TRAVERSAL),
            ("CHG-1", 8003, _EVIL_PATH_TRAVERSAL),
        ]:
            out = await _proxy_get(c, cid, port, path)
            assert "error" in out
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
