"""Hot-path benchmark on a realistically-shaped payload.

Reconstructs the live `NOC Engineer (Kimi)` agent's tool array from the agent export plus
the Tool Registry, so the measurement reflects the real tool count, real names, and real
descriptions rather than synthetic filler.

Reported, not asserted, except for one loose ceiling that catches a gross regression -
absolute timings on a shared CI box are not a stable assertion target.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import chat_body
from tool_filter_proxy.adapter import filter_body
from tool_filter_proxy.librechat_names import build_reverse_index
from tool_filter_proxy.metadata import RegistryMetadata

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_JSON = _REPO_ROOT / "apps" / "ai-platform" / "librechat" / "agents" / "agents.json"

pytestmark = pytest.mark.skipif(not _AGENTS_JSON.is_file(),
                                reason="agents.json export not present")


def _live_payload():
    """The real agent's tools, with registry descriptions and a plausible schema each."""
    from tool_registry import load_registry

    registry = load_registry()
    index = build_reverse_index(registry)
    agents = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))
    agent = next(a for a in agents if a["id"] == "agent_kimi26NOCEngineerV1")

    tools = []
    for name in (t for t in agent["tools"] if not t.startswith("sys__")):
        record = index.get(name)
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": record.description if record else "",
                "parameters": {
                    "type": "object",
                    "properties": {
                        f"param_{i}": {"type": "string", "description": "x" * 40}
                        for i in range(6)
                    },
                    "required": ["param_0"],
                },
            },
        })
    return tools


def test_benchmark_hot_path(capsys):
    tools = _live_payload()
    metadata = RegistryMetadata.load()
    body = chat_body(tools, user="Why is charger CHG-123 offline?", stream=True)
    raw = json.dumps(body).encode()

    # end-to-end: parse -> unwrap -> enrich -> rank -> pin -> guard -> reserialize
    def hot_path():
        parsed = json.loads(raw)
        new_body, decision = filter_body(parsed, metadata_provider=metadata, top_k=20)
        return json.dumps(new_body).encode(), decision

    out, decision = hot_path()          # warm caches
    iterations = 200
    start = time.perf_counter()
    for _ in range(iterations):
        hot_path()
    total_ms = (time.perf_counter() - start) * 1000 / iterations

    start = time.perf_counter()
    for _ in range(iterations):
        json.loads(raw)
    parse_ms = (time.perf_counter() - start) * 1000 / iterations

    parsed = json.loads(raw)
    start = time.perf_counter()
    for _ in range(iterations):
        filter_body(parsed, metadata_provider=metadata, top_k=20)
    filter_ms = (time.perf_counter() - start) * 1000 / iterations

    with capsys.disabled():
        print(f"\n  payload        : {len(tools)} tools, {len(raw)/1024:.1f} KB")
        print(f"  after filtering: {decision.tools_after} tools, {len(out)/1024:.1f} KB "
              f"({100 - 100*len(out)//len(raw)}% smaller)")
        print(f"  total hot path : {total_ms:.3f} ms")
        print(f"    JSON parse   : {parse_ms:.3f} ms")
        print(f"    filter+enrich: {filter_ms:.3f} ms")
        print(f"    reserialize  : {max(0.0, total_ms - parse_ms - filter_ms):.3f} ms")
        print(f"  fallback tier  : {decision.fallback_tier}")

    assert decision.applied
    assert 0 < decision.tools_after <= decision.tools_before
    assert total_ms < 50  # loose ceiling; observed ~1ms


def test_benchmark_large_catalog(capsys):
    """Headroom check well beyond today's catalog."""
    tools = _live_payload()
    tools += [{"type": "function",
               "function": {"name": f"synthetic_{i}_mcp_noc",
                            "description": f"synthetic filler tool {i}",
                            "parameters": {"type": "object", "properties": {}}}}
              for i in range(1500)]
    metadata = RegistryMetadata.load()
    raw = json.dumps(chat_body(tools, user="Why is charger CHG-123 offline?")).encode()

    def hot_path():
        parsed = json.loads(raw)
        nb, d = filter_body(parsed, metadata_provider=metadata, top_k=20)
        return json.dumps(nb).encode(), d

    out, decision = hot_path()
    start = time.perf_counter()
    for _ in range(50):
        hot_path()
    total_ms = (time.perf_counter() - start) * 1000 / 50

    with capsys.disabled():
        print(f"\n  {len(tools)} tools, {len(raw)/1024:.1f} KB -> "
              f"{decision.tools_after} tools in {total_ms:.3f} ms")

    assert 0 < decision.tools_after <= 30
