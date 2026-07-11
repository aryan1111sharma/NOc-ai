"""Integration tests: drive each tool through the in-memory FastMCP client."""

import pytest
from fastmcp import Client

from tests.fake_cms import build_test_server


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
async def test_list_charger_actions_catalog():
    async with Client(build_test_server()) as client:
        res = await client.call_tool(
            "cms_list_charger_actions", {"qcharger_id": "ab-sam-sim"})
    rows = res.data
    assert len(rows) == 2
    names = {r["API"] for r in rows}
    assert {"id", "reset"} <= names
    reset = next(r for r in rows if r["API"] == "reset")
    assert reset["ApiType"] == "POST"
    assert reset["ApiDescription"] == "Reset"
