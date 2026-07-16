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


from fastmcp import Client


async def _call(name, **kw):
    async with Client(build_test_server()) as client:
        return await client.call_tool(name, kw)


@pytest.mark.asyncio
async def test_get_charger_firmware_returns_unquoted_release():
    res = await _call("cms_get_charger_firmware", qcharger_id="ab-sam-sim")
    assert res.data["status"] == "ok"
    assert res.data["release"] == "release-1.2.65"   # NOT '"release-1.2.65"'
    assert res.data["qcharger_id"] == "ab-sam-sim"


@pytest.mark.asyncio
async def test_fleet_firmware_skips_offline_and_names_them():
    res = await _call("cms_get_fleet_firmware", search="ab-sam")
    d = res.data
    assert d["skipped_offline"] == 1
    assert d["skipped_offline_ids"] == ["ab-sam-00591"]      # ping 600 min ago
    assert "ab-sam-00591" not in [c["qcharger_id"] for c in d["chargers"]]
    assert d["queried"] == len(d["chargers"])
    assert sum(d["rollup"].values()) == d["queried"]         # the invariant


@pytest.mark.asyncio
async def test_fleet_firmware_include_offline_queries_them():
    res = await _call("cms_get_fleet_firmware", search="ab-sam", include_offline=True)
    assert res.data["skipped_offline"] == 0
    assert "ab-sam-00591" in [c["qcharger_id"] for c in res.data["chargers"]]


@pytest.mark.asyncio
async def test_fleet_firmware_caps_targets_and_reports_the_overflow():
    res = await _call("cms_get_fleet_firmware", search="ab-sam", limit=1)
    d = res.data
    assert d["queried"] == 1
    assert d["skipped_over_cap"] == 1        # 2 online match; 1 queried
    assert d["queried"] + d["skipped_offline"] + d["skipped_over_cap"] == 3


@pytest.mark.asyncio
async def test_fleet_firmware_requires_a_filter():
    with pytest.raises(Exception, match="search"):
        await _call("cms_get_fleet_firmware")


@pytest.mark.asyncio
async def test_fleet_firmware_explicit_ids_are_queried_and_rollup_groups_by_release():
    res = await _call("cms_get_fleet_firmware",
                      qcharger_ids=["ab-sam-sim", "ab-sam-00591"], include_offline=True)
    d = res.data
    assert d["rollup"].get("release-1.2.65") == 2
    assert sum(d["rollup"].values()) == d["queried"]


@pytest.mark.asyncio
async def test_fleet_firmware_rollup_buckets_a_failure_as_unreachable(monkeypatch):
    """A charger we couldn't reach must be counted, not dropped: rollup must still
    reconcile with `queried`, so 'we couldn't ask' never reads as 'it has no firmware'."""
    real = fleet_mod.query_device

    async def fail_one(client, cid, command, args=None, *, timeout=None):
        if cid == 1187:                      # ab-sam-00585
            raise RuntimeError("device silent")
        return await real(client, cid, command, args, timeout=timeout)

    monkeypatch.setattr(fleet_mod, "query_device", fail_one)
    res = await _call("cms_get_fleet_firmware", search="ab-sam")
    d = res.data
    assert d["rollup"].get("unreachable") == 1
    assert d["rollup"].get("release-1.2.65") == 1
    assert sum(d["rollup"].values()) == d["queried"] == 2
    assert [c["status"] for c in d["chargers"]
            if c["qcharger_id"] == "ab-sam-00585"] == ["timeout"]


@pytest.mark.asyncio
async def test_firmware_tools_never_reach_a_write(monkeypatch):
    """These tools are read-only. `release` is a GET, and nothing here may touch the
    gated write path, with or without a confirmation token."""
    seen: list[str] = []
    real = fleet_mod.query_device

    async def spy(client, cid, command, args=None, *, timeout=None):
        seen.append(command)
        return await real(client, cid, command, args, timeout=timeout)

    monkeypatch.setattr(fleet_mod, "query_device", spy)
    await _call("cms_get_charger_firmware", qcharger_id="ab-sam-sim")
    await _call("cms_get_fleet_firmware", search="ab-sam")
    # Only pins the command string the fleet layer issues. The actual GET-only
    # enforcement guard lives in test_query_rejects_write_command (test_device_commands.py).
    assert set(seen) == {"release"}, f"unexpected commands issued: {seen}"


@pytest.mark.asyncio
async def test_fleet_firmware_deduplicates_repeated_ids():
    res = await _call("cms_get_fleet_firmware",
                      qcharger_ids=["ab-sam-sim", "ab-sam-sim", "ab-sam-sim"],
                      include_offline=True)
    d = res.data
    assert d["queried"] == 1
    assert d["skipped_over_cap"] == 0
    assert sum(d["rollup"].values()) == d["queried"]


@pytest.mark.asyncio
async def test_fleet_firmware_search_matches_customer_name():
    """The docstring's own worked example -- "Ador's chargers" -- must actually work."""
    res = await _call("cms_get_fleet_firmware", search="Ador", include_offline=True)
    d = res.data
    assert d["queried"] == 3
    assert sum(d["rollup"].values()) == d["queried"]


@pytest.mark.asyncio
async def test_fleet_firmware_accepts_ids_case_insensitively():
    """cms_get_charger_firmware accepts any casing; the fleet tool must not disagree."""
    res = await _call("cms_get_fleet_firmware", qcharger_ids=["AB-SAM-SIM"],
                      include_offline=True)
    assert res.data["queried"] == 1
    assert res.data["chargers"][0]["status"] == "ok"
