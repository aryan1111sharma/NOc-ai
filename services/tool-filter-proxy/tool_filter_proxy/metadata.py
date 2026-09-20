"""Registry-backed metadata provider for the filtering core.

`load_registry()` reads nine YAML files and validates them against two JSON Schemas -
milliseconds, but far too much to repeat per request. It is loaded ONCE at startup into
a reverse index keyed by LibreChat's mangled tool name, after which a lookup is a single
dict hit.

Read-only by construction: this module imports only `load_registry` and never touches
`YamlToolConfigStore`, so the proxy cannot alter registry state.

Degradation is deliberate: if the registry is missing or malformed the proxy still
starts and still filters, using keyword + entity signals only. Losing the
domain/capability/server signals makes ranking weaker, not wrong - and a knowledge-base
problem must not take chat down.
"""

from __future__ import annotations

import logging

from .librechat_names import build_reverse_index

log = logging.getLogger(__name__)


class RegistryMetadata:
    """Callable in the shape `rank(metadata_provider=...)` expects: given a tool name,
    return a mapping of the facets the tool object itself lacks, or None."""

    def __init__(self, index: dict | None = None) -> None:
        self._index = index or {}

    @property
    def size(self) -> int:
        return len(self._index)

    @classmethod
    def load(cls) -> "RegistryMetadata":
        try:
            from tool_registry import load_registry

            registry = load_registry()
            index = build_reverse_index(registry)
            log.info("registry loaded: %d tools, %d resolvable by LibreChat name",
                     len(registry), len(index))
            return cls(index)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            log.warning("registry unavailable (%s); filtering continues without "
                        "domain/capability/risk enrichment", exc)
            return cls({})

    def __call__(self, librechat_name: str) -> dict | None:
        record = self._index.get(librechat_name)
        if record is None:
            return None
        return {
            "mcp_server": record.mcp_server,
            "domain": record.domain,
            "capability": record.capability,
            "risk": record.risk,
        }
