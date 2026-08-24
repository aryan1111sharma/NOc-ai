"""Integration tests for the diagnostics tools (log download / state-log export)."""

import json

import pytest
from fastmcp import Client

from tests.fake_noc_ops import build_test_server


def _payload(result):
    return json.loads(result.content[0].text)


@pytest.fixture
async def client():
    async with Client(build_test_server()) as c:
        yield c


async def test_diagnostics_tools_registered(client):
    tools = {t.name for t in await client.list_tools()}
    assert {"charger_request_log_download", "charger_export_state_logs"} <= tools


async def test_request_log_download_defaults(client):
    data = _payload(await client.call_tool("charger_request_log_download", {"charger_id": "CHG-1"}))
    assert data["job_id"] == "job-CHG-1"
    assert data["status"] == "queued"


async def test_export_state_logs_by_time_range(client):
    data = _payload(await client.call_tool("charger_export_state_logs", {
        "charger_id": "CHG-1", "from_ts": "2026-08-01T00:00:00Z", "to_ts": "2026-08-02T00:00:00Z",
    }))
    assert data["charger_id"] == "CHG-1"
    assert data["from_ts"] == "2026-08-01T00:00:00Z"


async def test_export_state_logs_offline_charger_error(client):
    data = _payload(await client.call_tool("charger_export_state_logs", {"charger_id": "OFFLINE"}))
    assert "error" in data
    assert "502" in data["error"]


async def test_export_state_logs_rejects_invalid_source_before_backend_call(client):
    data = _payload(await client.call_tool("charger_export_state_logs", {
        "charger_id": "CHG-1", "source": "not_a_real_source",
    }))
    assert "error" in data
    assert "source must be one of" in data["error"]
