"""Security regression tests for NOC *backend* URL construction (noc_mcp.http, noc_mcp.history).

Sibling of test_proxy_hardening.py, which covers the charger-proxy route. This file covers the
plain backend routes -- ``/api/v1/chargers/{charger_id}/history/...`` and friends -- that
``noc_mcp.history`` and ``control_center``'s history fallbacks read. They do NOT go through
``_validate_proxy_url``, and for a while nothing else validated them either.

The two confirmed reproductions this file pins
----------------------------------------------
1. **Verb override on live hardware.** ``_fetch_cached_ocpp_config`` GET a hand-built URL with
   no ``params=``, so a query string smuggled inside ``charger_id`` survived as REAL query
   parameters::

       _fetch_charger_ocpp_config(c, "CHG-1/proxy/8003/api/v1/pin/contactor/dual?method=POST&x=")
       URL   : /api/v1/chargers/CHG-1/proxy/8003/api/v1/pin/contactor/dual?method=POST&x=/ocpp-config/cache
       PARAMS: {'method': 'POST', 'x': '/ocpp-config/cache'}

   ``method`` is the parameter the NOC charger-proxy route uses to choose the verb it forwards
   to the charger, and the path is dual contactor actuation. The tool then returned success
   stamped ``source="history"``, so the agent believed it had read a cache.

   The cruel part: ``noc_charger_ocpp_config`` tries the live leg first via ``_proxy_get``,
   which *correctly rejects* the hostile id -- and that rejection is an ``is_error``, so
   ``live_or_history`` fell through to the unvalidated cached leg. The hardening on the live
   leg is what triggered the vulnerable path.

2. **Traversal into an excluded module.** Every other fetcher spliced the id straight in::

       _fetch_charger_history(c, "../../../api/v1/admin/users", limit=None)
       URL   : /api/v1/admin/users/history/sessions

   ``/api/v1/admin`` is excluded from the tool surface by ``server._EXCLUDED_PREFIXES`` on
   purpose; the id walked back out of ``/api/v1/chargers/`` and into it.

The other four fetchers passed ``params={...}``, which happened to overwrite the injected query
string -- luck, not design, and the reason only the cached-config leg escalated from GET-SSRF
to a verb override.
"""

from urllib.parse import unquote

import httpx
import pytest

from noc_mcp.auth import NocAuthClient
from noc_mcp.control_center import (
    _get_alarms_with_fallback,
    _get_ocpp_status_with_fallback,
    _get_session_details_with_fallback,
)
from noc_mcp.history import (
    _fetch_charger_history,
    _fetch_charger_ocpp_config,
    _fetch_charger_stability,
    _fetch_charger_telemetry,
    _fetch_session_events,
)
from noc_mcp.http import build_url, get
from noc_mcp.identifiers import validate_charger_id, validate_session_id
from tests.fake_noc import build_test_client

_LOGIN = "/api/v1/auth/login"

_WRITE_PATH = "api/v1/pin/contactor/dual"
_EVIL_VERB_OVERRIDE = f"CHG-1/proxy/8003/{_WRITE_PATH}?method=POST&x="
_EVIL_TRAVERSAL = "../../../api/v1/admin/users"
_EVIL_HIJACK = "CHG-1/proxy/8005/api/v1/devtools/logs/x"

_HOSTILE = (_EVIL_VERB_OVERRIDE, _EVIL_TRAVERSAL, _EVIL_HIJACK,
            "CHG-1?method=POST", "CHG-1#frag", "CHG-1/x", "a%2Fb", "a b", "a:b", "a\\b",
            ".", "..", "../..", "", "   ", None, 7)


def _capturing():
    """``(seen, handler)``: answers login, records and 200s every other request."""
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


# --------------------------------------------------------------------------------------
# identifiers.validate_session_id
# --------------------------------------------------------------------------------------
#
# The bundled spec declares session_id as {"type": "string"} and the
# /chargers/{charger_id}/sessions/{session_id}/events description names both shapes the backend
# must accept: a NUMERIC id (TimescaleDB genone_full_state fallback) and chargers that "use
# UUID session IDs". The class below is the union of those plus the short prefixed ids the
# fixtures use.

@pytest.mark.parametrize("session_id", [
    "S1", "S-old", "1", "0", "123456789",
    "550e8400-e29b-41d4-a716-446655440000",          # UUID, per the spec's own description
    "550E8400E29B41D4A716446655440000",
    "sess_2026-09-01", "a.b-c_d",
])
def test_real_session_ids_are_accepted(session_id):
    assert validate_session_id(session_id) is None


@pytest.mark.parametrize("session_id", [
    "S1/../../../api/v1/admin/users",
    "S1?method=POST",
    "S1/events?method=POST&x=",
    "S1#frag",
    "S1/x",
    "..",
    ".",
    "a%2Fb",
    "a b",
    "a:b",
    "a\\b",
    "",
    "   ",
    None,
    12,
])
def test_unsafe_session_ids_are_rejected(session_id):
    reason = validate_session_id(session_id)
    assert reason is not None and reason.startswith("ValueError: session_id")


def test_session_and_charger_validators_share_one_rule_set():
    """One definition of "safe identifier", two names -- not two drifting copies."""
    for value in ("CHG-1", "S1", "..", "a/b", "a?b", "", None):
        assert (validate_charger_id(value) is None) == (validate_session_id(value) is None)


# --------------------------------------------------------------------------------------
# noc_mcp.http.build_url
# --------------------------------------------------------------------------------------

def test_build_url_fills_and_leaves_a_legitimate_url_byte_identical():
    url, reason = build_url("/api/v1/chargers/{charger_id}/history/sessions",
                            charger_id="CHG-1")
    assert reason is None
    assert url == "/api/v1/chargers/CHG-1/history/sessions"


def test_build_url_fills_two_identifiers():
    url, reason = build_url("/api/v1/chargers/{charger_id}/sessions/{session_id}/events",
                            charger_id="QC_0012", session_id="550e8400-e29b-41d4-a716-446655440000")
    assert reason is None
    assert url == ("/api/v1/chargers/QC_0012/sessions/"
                   "550e8400-e29b-41d4-a716-446655440000/events")


@pytest.mark.parametrize("charger_id", _HOSTILE)
def test_build_url_rejects_a_hostile_charger_id(charger_id):
    url, reason = build_url("/api/v1/chargers/{charger_id}/history/sessions",
                            charger_id=charger_id)
    assert url is None and reason is not None


@pytest.mark.parametrize("session_id", ["S1?method=POST", "S1/../../admin", "..", "", None])
def test_build_url_rejects_a_hostile_session_id(session_id):
    url, reason = build_url("/api/v1/chargers/{charger_id}/sessions/{session_id}/events",
                            charger_id="CHG-1", session_id=session_id)
    assert url is None and reason is not None


@pytest.mark.parametrize("template", [
    # The backstop: a caller who hand-splices anyway is still caught on the assembled string.
    f"/api/v1/chargers/{_EVIL_VERB_OVERRIDE}/ocpp-config/cache",
    "/api/v1/chargers/../../../api/v1/admin/users/history/sessions",
    "/api/v1/reports/session-failures?method=POST",
    "/api/v1/reports/session#frag",
    "/api/v1/reports/a b",
    "/api/v1/reports//double",
    "/api/v1/reports/trailing/",
    "api/v1/reports/relative",
    "",
    None,
])
def test_build_url_rejects_an_unsafe_assembled_url_however_it_was_built(template):
    """Property 3 of noc_mcp/http.py: the assembled URL is re-validated regardless of origin.

    This is what makes the failure mode structural rather than a convention: a future call
    site that forgets the typed ``charger_id=`` keyword and f-strings the value in still
    cannot emit either confirmed exploit.
    """
    url, reason = build_url(template)
    assert url is None and reason is not None


def test_build_url_rejects_an_unfilled_or_unknown_placeholder():
    assert build_url("/api/v1/chargers/{charger_id}/x")[0] is None
    assert build_url("/api/v1/x/{gun_id}", gun_id=1)[0] is None


# --------------------------------------------------------------------------------------
# noc_mcp.http.get -- params are ALWAYS sent
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_always_sends_params_even_when_none_were_asked_for():
    """The single-line difference between the safe fetchers and the exploitable one."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        await get(c, "/api/v1/chargers/{charger_id}/history/sessions", charger_id="CHG-1")
    assert len(seen) == 1
    assert dict(seen[0].url.params) == {}
    assert seen[0].url.raw_path.decode() == "/api/v1/chargers/CHG-1/history/sessions"


@pytest.mark.asyncio
async def test_get_rejects_before_any_socket_is_touched():
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await get(c, "/api/v1/chargers/{charger_id}/history/sessions",
                        charger_id=_EVIL_VERB_OVERRIDE)
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert "error" in out and out["url"] == "/api/v1/chargers/{charger_id}/history/sessions"


@pytest.mark.asyncio
async def test_get_surfaces_status_and_body_on_a_non_2xx_without_raising():
    """``_fetch_cached_ocpp_config`` needs the 404 body to tell NO_CACHE from a real failure."""
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        return httpx.Response(404, json={"detail": "no cache", "code": "NO_CACHE"})

    async with _client(handler) as c:
        out = await get(c, "/api/v1/chargers/{charger_id}/ocpp-config/cache", charger_id="CHG-1")
    assert out["status"] == 404
    assert out["body"]["code"] == "NO_CACHE"
    assert "error" in out


@pytest.mark.asyncio
async def test_get_never_raises_on_a_transport_failure():
    def handler(request):
        if request.url.path.endswith(_LOGIN):
            return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})
        raise httpx.ConnectError("boom")

    async with _client(handler) as c:
        out = await get(c, "/api/v1/chargers", {})
    assert "error" in out and "ConnectError" in out["error"]


# --------------------------------------------------------------------------------------
# the two confirmed reproductions, closed
# --------------------------------------------------------------------------------------

_HISTORY_FETCHERS = [
    ("_fetch_charger_history",
     lambda c, cid: _fetch_charger_history(c, cid, limit=None)),
    ("_fetch_charger_telemetry",
     lambda c, cid: _fetch_charger_telemetry(c, cid, resolution=None, hours=None,
                                             from_ts=None, to_ts=None, limit=None)),
    ("_fetch_session_events",
     lambda c, cid: _fetch_session_events(c, cid, "S1")),
    ("_fetch_charger_stability",
     lambda c, cid: _fetch_charger_stability(c, cid)),
    ("_fetch_charger_ocpp_config",
     lambda c, cid: _fetch_charger_ocpp_config(c, cid)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("charger_id", _HOSTILE)
@pytest.mark.parametrize("name,call", _HISTORY_FETCHERS, ids=[n for n, _ in _HISTORY_FETCHERS])
async def test_every_history_fetcher_is_fail_closed_on_a_hostile_charger_id(
        name, call, charger_id):
    """Zero outbound requests -- matching ``_proxy_get``'s contract, not one request that fails."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await call(c, charger_id)
    assert seen == [], f"{name} made a request: {[str(r.url) for r in seen]}"
    assert "error" in out, f"{name} returned {out!r} instead of a structured error"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", ["S1?method=POST", "S1/../../../api/v1/admin/users",
                                        "..", "S1/x", "", None])
async def test_session_events_is_fail_closed_on_a_hostile_session_id(session_id):
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _fetch_session_events(c, "CHG-1", session_id)
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert "error" in out and "session_id" in out["error"]


@pytest.mark.asyncio
async def test_reproduction_1_verb_override_on_the_cached_ocpp_leg_is_closed():
    """The exact confirmed payload: no request, and certainly no ``method`` query parameter.

    The live leg's rejection must no longer be able to *cause* the cached leg to fire.
    """
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _fetch_charger_ocpp_config(c, _EVIL_VERB_OVERRIDE)
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert out.get("source") != "history", "the tool still claimed a successful cache read"
    assert "error" in out


@pytest.mark.asyncio
async def test_reproduction_2_traversal_into_the_admin_module_is_closed():
    seen, handler = _capturing()
    async with _client(handler) as c:
        out = await _fetch_charger_history(c, _EVIL_TRAVERSAL, limit=None)
    assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
    assert "error" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("charger_id", [_EVIL_VERB_OVERRIDE, _EVIL_TRAVERSAL, _EVIL_HIJACK])
async def test_control_center_history_fallbacks_stay_fail_closed(charger_id):
    """The three ``*_with_fallback`` helpers now reach the backend through noc_mcp.http too."""
    for call in (_get_ocpp_status_with_fallback,
                 lambda c, cid: _get_alarms_with_fallback(c, cid, None),
                 lambda c, cid: _get_session_details_with_fallback(c, cid, None)):
        seen, handler = _capturing()
        async with _client(handler) as c:
            out = await call(c, charger_id)
        assert seen == [], f"a request was made: {[str(r.url) for r in seen]}"
        assert "error" in out


# --------------------------------------------------------------------------------------
# legitimate behaviour is unchanged
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("charger_id", ["CHG-1", "QC_0012", "abc.def-9",
                                        "60f1a2b3c4d5e6f708192a3b", "..a", "a.."])
@pytest.mark.parametrize("name,call", _HISTORY_FETCHERS, ids=[n for n, _ in _HISTORY_FETCHERS])
async def test_valid_charger_ids_still_reach_the_same_urls(name, call, charger_id):
    """Byte-identical URLs for every legal id -- validation must cost no legitimate call."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        await call(c, charger_id)
    assert seen, f"{name} made no request for a legal id {charger_id!r}"
    for request in seen:
        decoded = unquote(request.url.raw_path.decode())
        assert decoded.startswith(f"/api/v1/chargers/{charger_id}/"), decoded
        assert request.method == "GET"
        assert "method" not in request.url.params


@pytest.mark.asyncio
async def test_history_urls_are_exactly_what_they_were_before_the_fix():
    """Pinned by hand so a refactor of the templates cannot quietly move an endpoint."""
    seen, handler = _capturing()
    async with _client(handler) as c:
        await _fetch_charger_history(c, "CHG-1", limit=5)
        await _fetch_charger_telemetry(c, "CHG-1", resolution="1h", hours=24,
                                       from_ts="2026-09-01T00:00:00Z", to_ts=None, limit=3)
        await _fetch_session_events(c, "CHG-1", "S1")
        await _fetch_charger_stability(c, "CHG-1")
    assert [r.url.raw_path.decode() for r in seen] == [
        "/api/v1/chargers/CHG-1/history/sessions?limit=5",
        "/api/v1/chargers/CHG-1/history/alarms?limit=5",
        "/api/v1/chargers/CHG-1/telemetry/latest",
        "/api/v1/chargers/CHG-1/telemetry/aggregated?resolution=1h&hours=24",
        "/api/v1/chargers/CHG-1/telemetry/history?limit=3&from_ts=2026-09-01T00%3A00%3A00Z",
        "/api/v1/chargers/CHG-1/sessions/S1/events",
        "/api/v1/chargers/CHG-1/stability",
        "/api/v1/chargers/CHG-1/commands",
        "/api/v1/chargers/CHG-1/ssh-status",
    ]


@pytest.mark.asyncio
async def test_all_five_history_tools_still_answer_normally():
    """End-to-end against the fake NOC: same payloads as before the hardening."""
    async with build_test_client() as c:
        history = await _fetch_charger_history(c, "CHG-1", limit=None)
        telemetry = await _fetch_charger_telemetry(c, "CHG-1", resolution=None, hours=None,
                                                   from_ts=None, to_ts=None, limit=None)
        events = await _fetch_session_events(c, "CHG-1", "S1")
        stability = await _fetch_charger_stability(c, "CHG-1")
        live_cfg = await _fetch_charger_ocpp_config(c, "CHG-1")
        cached_cfg = await _fetch_charger_ocpp_config(c, "OFFLINE")
        no_cache = await _fetch_charger_ocpp_config(c, "NOCACHE")

    assert history["sessions"]["sessions"][0]["session_id"] == "S0"
    assert history["alarms"]["alarms"][0]["code"] == "E12"
    assert telemetry["latest"]["kw"] == 42
    assert events["events"][0]["type"] == "SESSION_START"
    assert stability["ssh"]["connected"] is True
    assert live_cfg["source"] == "live" and live_cfg["HeartbeatInterval"] == "300"
    assert cached_cfg["source"] == "history" and cached_cfg["HeartbeatInterval"] == "60"
    assert no_cache["source"] == "history" and "error" not in no_cache


# --------------------------------------------------------------------------------------
# structural: nobody builds a backend GET by hand any more
# --------------------------------------------------------------------------------------

def test_no_feature_module_calls_client_get_directly():
    """The regression's root cause was two spellings of "GET the backend".

    ``noc_mcp/http.py`` owns backend GETs and ``control_center._proxy_get`` owns the charger
    proxy (it has its own, stricter ``_validate_proxy_url``). No feature module may open a
    third path -- that is how the ``params=``-less call got in.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "noc_mcp"
    offenders = {}
    for path in sorted(root.glob("*.py")):
        if path.name in ("http.py", "auth.py", "control_center.py"):
            continue
        source = path.read_text(encoding="utf-8")
        if "client.get(" in source or "client.request(" in source:
            offenders[path.name] = [line.strip() for line in source.splitlines()
                                    if "client.get(" in line or "client.request(" in line]
    assert offenders == {}, f"hand-rolled backend calls: {offenders}"


def test_control_center_only_hand_builds_the_validated_proxy_url():
    """The one exemption above, pinned: exactly one such call, inside ``_proxy_get``."""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1]
              / "noc_mcp" / "control_center.py").read_text(encoding="utf-8")
    assert source.count("client.get(") == 1
    proxy_get = source.split("async def _proxy_get(")[1].split("\ndef ")[0]
    assert "client.get(" in proxy_get
