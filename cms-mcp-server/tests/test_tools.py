"""Integration tests: drive each tool through the in-memory FastMCP client."""

import httpx
import pytest
from fastmcp import Client

from cms_mcp.auth import CmsAuthClient
from cms_mcp.server import build_server
from tests.fake_cms import _handler, build_test_server


def _recording_server():
    """A test server whose every outbound (path, content-type) is recorded."""
    seen: list[tuple[str, str]] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("content-type", "").lower()))
        return _handler(request)

    client = CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                           transport=httpx.MockTransport(recording))
    return build_server(client), seen


@pytest.mark.asyncio
async def test_datatables_actions_are_posted_form_urlencoded():
    """Regression: the legacy binder answers 200 + EMPTY BODY when a DataTables
    action is labelled application/json, so these tools silently returned [] for
    every charger in production while the suite stayed green. The live page posts
    a JSON string under jQuery's urlencoded default -- pin that."""
    server, seen = _recording_server()
    async with Client(server) as c:
        await c.call_tool("cms_get_charger_sessions", {"qcharger_id": "ab-sam-sim"})
        await c.call_tool("cms_get_charger_alerts", {"qcharger_id": "ab-sam-sim"})
        await c.call_tool("cms_get_charger_action_history", {"qcharger_id": "ab-sam-sim"})

    for action in ("/GetSessionLogsByChargerID", "/GetAlertLogsByChargerID",
                   "/GetApiExecutionDetailsByID"):
        cts = [ct for path, ct in seen if path.endswith(action)]
        assert cts, f"{action} was never called"
        assert all("application/x-www-form-urlencoded" in ct for ct in cts), \
            f"{action} must be form-urlencoded, got {cts}"


@pytest.mark.asyncio
async def test_readjsonlogfile_is_posted_as_json():
    """The mirror image of the above: ReadJsonLogFile binds only application/json
    (form-urlencoded gets the same silent empty body), so the two encodings are
    per-endpoint and must not be 'unified'."""
    server, seen = _recording_server()
    async with Client(server) as c:
        await c.call_tool("cms_read_device_log",
                          {"qcharger_id": "ab-sam-sim", "path": "E:\\ApiResponses\\r.txt"})
    cts = [ct for path, ct in seen if path.endswith("/ReadJsonLogFile")]
    assert cts, "ReadJsonLogFile was never called"
    assert all("application/json" in ct for ct in cts), cts


@pytest.mark.asyncio
async def test_get_charger_details_merges_and_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_details", {"qcharger_id": "ab-sam-sim"})
    d = res.data
    # merged metadata + live status
    assert d["QChargerID"] == "ab-sam-sim"
    assert d["Description"] == "Dual CCS"
    assert d["OCPPID"] == "noc_sim"
    assert d["Gun_A"] == 2
    # last-event timestamp survives via the raw key (str-prefixed is null here)
    assert d["Event_Timestamp"] == "/Date(1781076309716)/"
    assert "strEvent_Timestamp" not in d   # null variant dropped
    # server filesystem-path noise trimmed out
    assert "StateLogPath" not in d


@pytest.mark.asyncio
async def test_get_charger_sessions_trims_and_limits():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_sessions", {"qcharger_id": "ab-sam-sim", "limit": 5})
    rows = res.data
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["Description"] == "SESSION_STOP"
    assert row["ChargerIDGun"] == "secc2300-21641"
    assert "StateLogPath" not in row   # filesystem-path noise dropped


@pytest.mark.asyncio
async def test_get_charger_alerts_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_alerts", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 1
    row = rows[0]
    assert row["Description"] == "ALERT"
    assert row["ErrorDesc"] == "NoError"
    assert row["Src"] == "stack"
    assert "FullStatePath" not in row


@pytest.mark.asyncio
async def test_get_charger_action_history_trims():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_get_charger_action_history", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 1
    row = rows[0]
    assert row["API"] == "id"
    assert row["ApiType"] == "GET"
    assert row["Description"] == "Executed"
    assert row["ExecutionID"] == 414827
    assert "Output" not in row   # server filesystem path dropped


@pytest.mark.asyncio
async def test_empty_body_raises_rather_than_reading_as_no_rows():
    """An empty body is a degraded read, never 'no rows'. Silently returning {} zeroed
    the correlation baseline and let a years-old row be reported as current firmware."""
    def empty(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Home/GetUsersByEmailAndPassword"):
            return httpx.Response(200, json={"UserID": 1},
                                  headers={"set-cookie": ".ASPXAUTH=abc; path=/"})
        return httpx.Response(200, content=b"", headers={"content-type": "application/json"})

    client = CmsAuthClient(base_url="http://cms.test", user="u", password="p",
                           transport=httpx.MockTransport(empty))
    from cms_mcp.auth import CmsUnavailableError
    from cms_mcp.server import _post_datatables
    async with client:
        with pytest.raises(CmsUnavailableError):
            await _post_datatables(client, "/ChargerDetails/GetApiExecutionDetailsByID", 1429, 5)


@pytest.mark.asyncio
async def test_list_charger_actions_catalog():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_list_charger_actions", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 3
    names = {r["API"] for r in rows}
    assert {"id", "reset", "release"} <= names
    reset = next(r for r in rows if r["API"] == "reset")
    assert reset["ApiType"] == "POST"
    assert reset["ApiDescription"] == "Reset"
