"""Fleet fan-out engine and the firmware tools."""

import asyncio
import json

import httpx
import pytest

from cms_mcp import fleet as fleet_mod
from cms_mcp.auth import CmsUnavailableError
from cms_mcp.config import settings
from cms_mcp.fleet import _unwrap, fan_out, load_fleet_status
from tests.fake_cms import build_test_client, build_test_server, reset_execution_state


@pytest.fixture(autouse=True)
def _reset_exec_state():
    # fan_out schedules real ExecuteApi calls against the shared fake-CMS execution
    # store; reset it so these tests don't leak history rows into other test modules
    # (e.g. test_tools.py's exact-count assertions on charger action history).
    reset_execution_state()
    yield
    reset_execution_state()


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


@pytest.mark.asyncio
async def test_fan_out_classifies_each_charger():
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429}, "id", timeout=5.0)
    assert rows[0]["qcharger_id"] == "ab-sam-sim"
    assert rows[0]["status"] == "ok"
    # A non-string device payload passes through _unwrap untouched. The unquoting
    # path is exercised by the `release` command, whose live payload is a
    # double-encoded string (covered in the next task).
    assert rows[0]["result"] == {"api": "id", "value": "device-ok"}


@pytest.mark.asyncio
async def test_fan_out_marks_unsupported_when_catalog_lacks_command():
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429}, "no-such-command", timeout=5.0)
    assert rows[0]["status"] == "unsupported"
    assert rows[0]["result"] is None


@pytest.mark.asyncio
async def test_fan_out_reports_a_cms_flap_as_cms_error_not_timeout(monkeypatch):
    """The whole point: a backend flap must never be blamed on the charger."""
    async def flap(client, cid, command, args=None, *, timeout=None):
        raise CmsUnavailableError(httpx.Request("POST", "http://cms.test/x"),
                                  httpx.Response(502))

    monkeypatch.setattr(fleet_mod, "query_device", flap)
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429}, "id", timeout=5.0)
    assert rows[0]["status"] == "cms_error"


@pytest.mark.asyncio
async def test_fan_out_never_exceeds_configured_concurrency(monkeypatch):
    monkeypatch.setattr(settings, "fleet_concurrency", 2)
    live = {"now": 0, "peak": 0}
    real = fleet_mod.query_device

    async def spy(client, cid, command, args=None, *, timeout=None):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        try:
            await asyncio.sleep(0.05)
            return await real(client, cid, command, args, timeout=timeout)
        finally:
            live["now"] -= 1

    monkeypatch.setattr(fleet_mod, "query_device", spy)
    targets = {f"c{i}": 1429 for i in range(8)}
    async with build_test_client() as client:
        await fan_out(client, targets, "id", timeout=5.0)
    assert live["peak"] <= 2, f"peak concurrency {live['peak']} exceeded cap 2"


@pytest.mark.asyncio
async def test_fan_out_one_failure_does_not_fail_the_batch(monkeypatch):
    real = fleet_mod.query_device

    async def fail_one(client, cid, command, args=None, *, timeout=None):
        if cid == 1187:
            raise RuntimeError("device silent")
        return await real(client, cid, command, args, timeout=timeout)

    monkeypatch.setattr(fleet_mod, "query_device", fail_one)
    async with build_test_client() as client:
        rows = await fan_out(client, {"ab-sam-sim": 1429, "ab-sam-00585": 1187},
                             "id", timeout=5.0)
    by_id = {r["qcharger_id"]: r for r in rows}
    assert by_id["ab-sam-sim"]["status"] == "ok"
    assert by_id["ab-sam-00585"]["status"] == "timeout"
