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
