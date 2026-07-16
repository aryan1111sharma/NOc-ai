"""Unit + integration tests for the CMS device-command tools."""


def test_device_settings_defaults():
    from cms_mcp.config import settings
    assert settings.device_poll_timeout == 20.0
    assert settings.device_poll_interval == 2.0
    assert settings.device_pending_ttl_seconds == 120
    assert settings.device_audit_log_file is None


import httpx
import pytest
from cms_mcp.auth import CmsAuthClient


def _login_ok(request):
    return httpx.Response(200, json={"UserID": 74},
                          headers={"set-cookie": ".ASPXAUTH=abc; path=/"})


@pytest.mark.asyncio
async def test_login_captures_user_id():
    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        return httpx.Response(200, json=[])

    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        assert c.user_id == 0            # not logged in yet
        await c._ensure_login()
        assert c.user_id == 74


def test_write_allowlist_membership():
    from cms_mcp.device_commands import WRITE_ALLOWLIST
    assert "reset" in WRITE_ALLOWLIST
    assert "ocpp-client/restart" in WRITE_ALLOWLIST
    assert "outlets/{number}/stop" in WRITE_ALLOWLIST
    # Tier-3 / off-list must NOT be present
    assert "scripts/active" not in WRITE_ALLOWLIST
    assert "outlets/{number}/e-stop" not in WRITE_ALLOWLIST
    assert len(WRITE_ALLOWLIST) == 8


def test_resolve_path_substitutes_and_validates():
    from cms_mcp.device_commands import resolve_path
    assert resolve_path("ocpp-client/restart", None) == "ocpp-client/restart"
    assert resolve_path("outlets/{number}/stop", {"number": 1}) == "outlets/1/stop"
    with __import__("pytest").raises(ValueError) as ei:
        resolve_path("outlets/{number}/stop", {})
    assert "number" in str(ei.value)


def test_log_path_guard():
    from cms_mcp.device_commands import is_safe_log_path
    assert is_safe_log_path(r"E:\ApiResponses\r.txt")
    assert is_safe_log_path(r"E:\CMS_Chargers_State_Logs\x.json")
    assert not is_safe_log_path(r"E:\Windows\System32\config\SAM")
    assert not is_safe_log_path(r"..\..\secret.txt")
    assert not is_safe_log_path(r"E:\ApiResponses\r.exe")


def test_pending_token_single_use_and_expiry():
    from cms_mcp import device_commands as dc
    dc._PENDING.clear()
    token = dc._mint_pending({"charger": "x"})
    assert isinstance(token, str) and len(token) > 20
    got = dc._pop_pending(token, ttl=120)
    assert got["charger"] == "x"
    assert dc._pop_pending(token, ttl=120) is None   # single-use
    # expired
    token2 = dc._mint_pending({"charger": "y"})
    dc._PENDING[token2]["created_at"] -= 999
    assert dc._pop_pending(token2, ttl=120) is None


def test_resolve_path_rejects_arg_injection():
    from cms_mcp.device_commands import resolve_path
    import pytest as _pytest
    for bad in ["1/../reset", "1/2", r"a\b", ".."]:
        with _pytest.raises(ValueError) as ei:
            resolve_path("outlets/{number}/stop", {"number": bad})
        assert "number" in str(ei.value)
    # a normal single-segment value still works
    assert resolve_path("outlets/{number}/stop", {"number": 3}) == "outlets/3/stop"


@pytest.mark.asyncio
async def test_post_wrapped_builds_vvm_body():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        seen["body"] = request.content.decode()
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(200, json="API scheduled successfully !")

    from cms_mcp import device_commands as dc
    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        resp = await dc._post_wrapped(c, dc._EXECUTE_PATH, "vvm",
                                      {"ChargerID": 5, "ApiID": 1})
    assert resp.json() == "API scheduled successfully !"
    assert seen["body"].startswith("{vvm: ")
    assert '"ChargerID": 5' in seen["body"]
    assert "application/json" in seen["ctype"]


@pytest.mark.asyncio
async def test_load_catalog_maps_api_to_row():
    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        if request.url.path.endswith("/GetAllApisByChargerID"):
            return httpx.Response(200, json=[
                {"ApiID": 1, "API": "id", "ApiType": "GET", "IsInputRequired": False},
                {"ApiID": 154, "API": "reset", "ApiType": "POST", "IsInputRequired": False},
            ])
        return httpx.Response(404)

    from cms_mcp import device_commands as dc
    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        cat = await dc._load_catalog(c, 1429)
    assert cat["id"]["ApiID"] == 1 and cat["id"]["ApiType"] == "GET"
    assert cat["reset"]["ApiID"] == 154


from fastmcp import Client
from fake_cms import build_test_server, reset_execution_state


@pytest.fixture(autouse=True)
def _reset_exec_state():
    reset_execution_state()
    yield
    reset_execution_state()


async def _call(name, **kw):
    async with Client(build_test_server()) as client:
        return await client.call_tool(name, kw)


@pytest.mark.asyncio
async def test_action_prepare_rejects_off_allowlist():
    with pytest.raises(Exception) as ei:
        await _call("cms_run_device_action_prepare",
                    qcharger_id="ab-sam-sim", command="scripts/active")
    assert "allow-list" in str(ei.value)


@pytest.mark.asyncio
async def test_action_execute_rejects_bad_token():
    res = await _call("cms_run_device_action_execute", confirmation_token="nope")
    assert "error" in res.data


@pytest.mark.asyncio
async def test_query_rejects_write_command():
    with pytest.raises(Exception) as ei:
        await _call("cms_run_device_query", qcharger_id="ab-sam-sim", command="reset")
    assert "read-only" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_read_device_log_path_guard():
    with pytest.raises(Exception) as ei:
        await _call("cms_read_device_log", qcharger_id="ab-sam-sim",
                    path=r"E:\Windows\System32\x.txt")
    msg = str(ei.value).lower()
    assert "state-log" in msg or "path must" in msg


@pytest.mark.asyncio
async def test_query_end_to_end_reads_device_output(monkeypatch):
    from cms_mcp.config import settings
    monkeypatch.setattr(settings, "device_poll_interval", 0.01)
    monkeypatch.setattr(settings, "device_poll_timeout", 2.0)
    res = await _call("cms_run_device_query", qcharger_id="ab-sam-sim", command="id")
    assert "device-ok" in str(res.data)


@pytest.mark.asyncio
async def test_action_prepare_then_execute_schedules(monkeypatch):
    from cms_mcp.config import settings
    monkeypatch.setattr(settings, "device_poll_interval", 0.01)
    monkeypatch.setattr(settings, "device_poll_timeout", 2.0)
    prep = await _call("cms_run_device_action_prepare",
                       qcharger_id="ab-sam-sim", command="reset")
    token = prep.data["confirmation_token"]
    res = await _call("cms_run_device_action_execute", confirmation_token=token)
    assert "scheduled successfully" in str(res.data)


@pytest.mark.asyncio
async def test_execute_api_not_retried_on_transient_flap():
    """A transient flap on the ExecuteApi WRITE must not be retried (would double-fire)."""
    from cms_mcp import device_commands as dc
    from cms_mcp.auth import CmsUnavailableError
    calls = {"execute": 0}

    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        if request.url.path.endswith("/ExecuteApi"):
            calls["execute"] += 1
            return httpx.Response(502, text="flap")   # transient
        return httpx.Response(200, json=[])

    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CmsUnavailableError):
            await dc._post_wrapped(c, dc._EXECUTE_PATH, "vvm",
                                   {"ChargerID": 1, "ApiID": 1}, idempotent=False)
    assert calls["execute"] == 1   # fired exactly once, NOT retried


@pytest.mark.asyncio
async def test_idempotent_post_still_retries_on_flap():
    """Read-via-POST calls must KEEP retrying a transient flap (v0.4.3 behavior)."""
    from cms_mcp import device_commands as dc
    calls = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return _login_ok(request)
        if request.url.path.endswith("/ReadJsonLogFile"):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, text="flap")   # transient once
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=[])

    async with CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                             transport=httpx.MockTransport(handler)) as c:
        out = await dc._read_log(c, r"E:\ApiResponses\r.txt")
    assert out == {"ok": True}
    assert calls["n"] == 2   # retried after the flap


def test_resolve_path_rejects_percent_and_unicode():
    from cms_mcp.device_commands import resolve_path
    import pytest as _pytest
    for bad in ["..%2f..%2freset", "1%2e%2e", "1⁄reset"]:  # %-encoded / unicode slash
        with _pytest.raises(ValueError):
            resolve_path("outlets/{number}/stop", {"number": bad})
    # a legit dotted script name (named GET read) still resolves
    assert resolve_path("scripts/installed/{script}", {"script": "foo.py"}) == \
        "scripts/installed/foo.py"


from cms_mcp import device_commands as dc
from cms_mcp.auth import CmsUnavailableError


def _flap() -> CmsUnavailableError:
    return CmsUnavailableError(httpx.Request("POST", "http://cms.test/x"),
                               httpx.Response(502))


@pytest.mark.asyncio
async def test_await_result_raises_cms_error_when_flapping_not_timeout(monkeypatch):
    """A flapping CMS must NOT be reported as a device/charger problem."""
    async def always_flap(client, charger_id, n):
        raise _flap()

    monkeypatch.setattr(dc, "_history_rows", always_flap)
    with pytest.raises(CmsUnavailableError):
        await dc._await_result(None, 1429, "release", 0, timeout=0.3, interval=0.01)


@pytest.mark.asyncio
async def test_await_result_returns_none_on_clean_timeout(monkeypatch):
    """Polls succeeded, device never answered => a genuine timeout, not a CMS error."""
    async def no_rows(client, charger_id, n):
        return []

    monkeypatch.setattr(dc, "_history_rows", no_rows)
    assert await dc._await_result(None, 1429, "release", 0,
                                  timeout=0.2, interval=0.01) is None


@pytest.mark.asyncio
async def test_await_result_survives_a_transient_flap_then_succeeds(monkeypatch):
    """A flap on ONE poll must not fail the query if a later poll finds the row."""
    calls = {"n": 0}
    row = {"ExecutionID": 5, "API": "release", "StatusID": 2,
           "Output": "E:\\ApiResponses\\r.txt"}

    async def flap_once(client, charger_id, n):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _flap()
        return [row]

    monkeypatch.setattr(dc, "_history_rows", flap_once)
    hit = await dc._await_result(None, 1429, "release", 0, timeout=2.0, interval=0.01)
    assert hit is not None and hit["ExecutionID"] == 5


@pytest.mark.asyncio
async def test_history_rows_does_not_swallow_cms_unavailable(monkeypatch):
    """The regression test for the actual bug: _history_rows used to wrap its poll in a
    bare `except Exception: return []`, making a flapping CMS look identical to "no rows
    yet" -- which surfaced to users as "the charger may be offline". A flap must escape."""
    async def flap(client, path, charger_id, n):
        raise _flap()

    monkeypatch.setattr(dc, "_post_datatables", flap)
    with pytest.raises(CmsUnavailableError):
        await dc._history_rows(None, 1429, 5)


@pytest.mark.asyncio
async def test_history_rows_returns_empty_when_there_are_no_rows(monkeypatch):
    """The other half: a genuinely empty history is [], not an error."""
    async def empty(client, path, charger_id, n):
        return {"draw": 1, "data": []}

    monkeypatch.setattr(dc, "_post_datatables", empty)
    assert await dc._history_rows(None, 1429, 5) == []
