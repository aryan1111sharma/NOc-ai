"""Route-map pruning and the read-only safety invariant."""

import pytest

from tests.fake_noc import build_test_server

# Fragments that must contribute zero tools.
_EXCLUDED_FRAGMENTS = [
    "api_v1_auth", "api_v1_admin", "api_v1_roles", "api_v1_permissions", "api_v1_teams",
    "api_v1_reports", "api_v1_stats",
]

# Curated tools that must exist after pruning.
_REQUIRED = {
    "noc_report", "noc_stats", "noc_fleet_status", "noc_charger_history",
    "noc_charger_telemetry", "noc_session_events", "noc_charger_stability",
    "noc_charger_ocpp_config", "get_control_center_api_catalog",
    "charger_get_charging_status", "charger_ssh_prepare", "charger_ssh_execute",
}


async def _names():
    return {t.name for t in await build_test_server().list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment", _EXCLUDED_FRAGMENTS)
async def test_excluded_modules_register_no_tools(fragment):
    offenders = sorted(n for n in await _names() if fragment in n)
    assert offenders == [], f"{fragment} still registers: {offenders}"


@pytest.mark.asyncio
async def test_curated_tools_are_present():
    missing = _REQUIRED - await _names()
    assert missing == set()


@pytest.mark.asyncio
async def test_charger_proxy_survives_pruning():
    # The ~110-endpoint on-charger long tail is reached through this; it must not be pruned.
    assert any("proxy" in n for n in await _names())


@pytest.mark.asyncio
async def test_surface_is_materially_smaller():
    total = len(await _names())
    assert total < 130, f"expected pruning to land under 130 tools, got {total}"


@pytest.mark.asyncio
async def test_no_registered_tool_can_issue_a_non_get():
    """Safety invariant: this server must never expose a write path to a charger."""
    for name in await _names():
        assert not name.endswith(("_post", "_put", "_delete", "_patch")), \
            f"non-GET tool registered: {name}"
