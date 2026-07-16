"""Fleet fan-out engine and the firmware tools."""

import json

import pytest

from cms_mcp.fleet import _unwrap, load_fleet_status
from tests.fake_cms import build_test_client


def test_unwrap_strips_double_encoding():
    # ReadJsonLogFile hands back a JSON string containing a JSON string.
    assert _unwrap(json.loads('"\\"release-1.2.65\\""')) == "release-1.2.65"


def test_unwrap_passes_through_plain_values():
    assert _unwrap("release-1.2.65") == "release-1.2.65"
    assert _unwrap("  release-1.2.65  ") == "release-1.2.65"
    assert _unwrap({"a": 1}) == {"a": 1}
    assert _unwrap(None) is None


@pytest.mark.asyncio
async def test_load_fleet_status_marks_online_by_ping_recency():
    async with build_test_client() as client:
        status = await load_fleet_status(client)
    assert status["ab-sam-sim"]["online"] is True
    assert status["ab-sam-sim"]["charger_id"] == 1429
    assert status["ab-sam-00591"]["online"] is False   # ping 600 min ago
