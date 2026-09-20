"""Tolerant adapter from whatever shape a caller's tool objects have into the fields
scoring needs.

WHY THIS EXISTS: Policy/Agent Access is designed but not live. Today the authorized set
comes from LibreChat / a raw MCP tool listing, whose entries carry only a name and a
description - no `domain`, `capability`, `risk`, or `mcp_server`. Without this adapter,
attribute access on those fields raises, `rank()` fails open, and every tool is returned
unfiltered - safe, but the whole point of filtering is lost. This module lets each signal
degrade independently: a missing `domain` disables only the domain signal, leaving
keyword/entity/server matching fully functional.

IDENTITY PRESERVATION (load-bearing for the subset invariant): `NormalizedTool.original`
holds the caller's own object, and `engine.py` returns THOSE objects, never these
wrappers. So `result ⊆ allowed_tools` holds by object identity, not merely by name
equality - a caller gets back exactly the objects it passed in.

Nothing here invents a classification. An absent `capability`/`risk`/`domain` stays
`None`, so its scoring term contributes zero rather than a guessed value. A caller that
wants those signals can supply real metadata via `rank(..., metadata_provider=...)`
(see engine.py) - that is the seam where Tool Registry enrichment plugs in later,
without this package importing the registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_NAME_FIELDS = ("tool_name", "name")
_SERVER_FIELDS = ("mcp_server", "server", "server_name")
_DOMAIN_FIELDS = ("domain", "category")
_DESCRIPTION_FIELDS = ("description", "desc")
_CAPABILITY_FIELDS = ("capability",)
_RISK_FIELDS = ("risk",)


@dataclass(frozen=True)
class NormalizedTool:
    tool_name: str
    mcp_server: str | None
    domain: str | None
    description: str
    capability: str | None
    risk: str | None
    original: Any


def _first_attr(tool: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(tool, dict):
        for field in fields:
            value = tool.get(field)
            if value is not None:
                return value
        return None
    for field in fields:
        value = getattr(tool, field, None)
        if value is not None:
            return value
    return None


class NormalizationError(ValueError):
    """A tool object has no usable name - the one field with no sensible default,
    since every index and every result is keyed by it."""


def normalize_tool(tool: Any, metadata_provider=None) -> NormalizedTool:
    """Adapt one caller-supplied tool object (dataclass, plain object, or dict).

    `metadata_provider`, when given, is called as `metadata_provider(tool_name)` and may
    return a mapping supplying any of domain/capability/risk/mcp_server that the tool
    object itself lacks. It never overrides a field the object already has, and it is
    only consulted for missing fields - so it can enrich, never contradict.
    """
    name = _first_attr(tool, _NAME_FIELDS)
    if not isinstance(name, str) or not name:
        raise NormalizationError(f"tool object has no usable name field: {type(tool).__name__}")

    server = _first_attr(tool, _SERVER_FIELDS)
    domain = _first_attr(tool, _DOMAIN_FIELDS)
    description = _first_attr(tool, _DESCRIPTION_FIELDS) or ""
    capability = _first_attr(tool, _CAPABILITY_FIELDS)
    risk = _first_attr(tool, _RISK_FIELDS)

    if metadata_provider is not None and None in (server, domain, capability, risk):
        extra = metadata_provider(name) or {}
        server = server if server is not None else extra.get("mcp_server")
        domain = domain if domain is not None else extra.get("domain")
        capability = capability if capability is not None else extra.get("capability")
        risk = risk if risk is not None else extra.get("risk")

    return NormalizedTool(
        tool_name=name,
        mcp_server=server if isinstance(server, str) else None,
        domain=domain if isinstance(domain, str) else None,
        description=description if isinstance(description, str) else str(description),
        capability=capability if isinstance(capability, str) else None,
        risk=risk if isinstance(risk, str) else None,
        original=tool,
    )


def normalize_tools(tools, metadata_provider=None) -> list[NormalizedTool]:
    """Normalize an iterable, skipping unusable entries rather than failing the whole
    request - one malformed tool in a listing of hundreds should not disable filtering
    (and a skipped tool is simply never offered, which is a reduction, never an
    expansion, of what the caller authorized)."""
    out: list[NormalizedTool] = []
    for tool in tools:
        try:
            out.append(normalize_tool(tool, metadata_provider))
        except NormalizationError:
            continue
    return out
