"""Proves filtering works against REAL Tool Registry data, not just local fakes -
without filtering's own library code importing or depending on the registry package
(only this test does, to exercise real interop).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REGISTRY_DIR = Path(__file__).resolve().parents[2] / "registry"
if str(_REGISTRY_DIR) not in sys.path:
    sys.path.insert(0, str(_REGISTRY_DIR))

try:
    from tool_registry import load_registry
    _REGISTRY_AVAILABLE = True
except ImportError:
    _REGISTRY_AVAILABLE = False

from tool_filtering import MAX_K, rank

pytestmark = pytest.mark.skipif(
    not _REGISTRY_AVAILABLE, reason="registry package / its deps (PyYAML, jsonschema) not installed in this venv"
)


def test_ranks_over_the_real_registrys_noc_tools():
    registry = load_registry()  # {tool_name: ToolRecord} - real 233-tool registry
    noc_tools = [rec for rec in registry.values() if rec.mcp_server == "noc" and rec.enabled]
    assert len(noc_tools) > MAX_K  # sanity: this is genuinely exercising narrowing, not passthrough

    result = rank("Why is charger CHG-123 offline?", {}, noc_tools)

    result_names = {t.tool_name for t in result.tools}
    assert result_names <= {t.tool_name for t in noc_tools}
    assert len(result.tools) <= MAX_K
    assert any("charger" in name.lower() for name in result_names)


def test_never_surfaces_a_tool_excluded_from_the_authorized_slice():
    registry = load_registry()
    noc_tools = [rec for rec in registry.values() if rec.mcp_server == "noc"]
    # Exclude the single most dangerous real tool in the inventory from the authorized
    # slice, then ask for exactly the kind of request that would otherwise surface it.
    allowed = [t for t in noc_tools if t.tool_name != "charger_ssh_execute"]

    result = rank("execute a command on the charger over ssh", {}, allowed)

    assert "charger_ssh_execute" not in {t.tool_name for t in result.tools}
