"""Read-only view of the provider credentials/models LibreChat already has
registered, in `librechat.yaml`'s `endpoints.custom` list.

This is the ONLY source of truth for "which credential + model pairs exist".
There is no second credential system here and no copied API keys: the
router reads the exact same file the LibreChat `api` container reads
(mounted read-only, see docker-compose.yml), and resolves `${VAR}`
placeholders in `apiKey` against its own process environment -- populated
from the same `.env` LibreChat itself uses, not a duplicate. It never writes
to librechat.yaml.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class CatalogTarget:
    """One selectable (credential, model) pair, exactly as an admin should
    see it: an endpoint name and a model. Never a key."""

    endpoint: str
    model: str

    def masked(self) -> dict:
        return {"endpoint": self.endpoint, "model": self.model}


@dataclass(frozen=True)
class ResolvedTarget:
    """The real values needed to call a provider. Only ever handed to
    llm_router.proxy -- never serialized into an HTTP response."""

    base_url: str
    api_key: str


def _resolve_env(value: str) -> str:
    """`"${GLM_API_KEY}"` -> the real environment value. A literal string
    (librechat.yaml's schema allows one) passes through unchanged."""
    match = _ENV_VAR.match(value or "")
    if not match:
        return value or ""
    return os.environ.get(match.group(1), "")


class Catalog:
    """Parses `endpoints.custom` on every call -- same "simple rather than
    clever" choice as Registry (see store.py): this is an admin-facing,
    low-QPS surface, not a hot path, and the file is a few KB.

    `endpoints`, when given, overrides reading `path` entirely -- used by
    tests to supply a fixed list without touching a filesystem or the
    environment.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        self_endpoint_name: str = "NOC Router",
        endpoints: list[dict] | None = None,
    ):
        self._path = Path(path) if path else None
        self._self_name = self_endpoint_name
        self._static_endpoints = endpoints

    def _load_endpoints(self) -> list[dict]:
        if self._static_endpoints is not None:
            source = self._static_endpoints
        elif self._path is not None and self._path.exists():
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            source = ((raw.get("endpoints") or {}).get("custom")) or []
        else:
            source = []
        # Exclude the router's own custom-endpoint entry: routing to itself
        # would be an infinite loop, not a fallback target.
        return [e for e in source if e.get("name") != self._self_name]

    def list_targets(self) -> list[CatalogTarget]:
        targets: list[CatalogTarget] = []
        for endpoint in self._load_endpoints():
            name = endpoint.get("name", "")
            models = (endpoint.get("models") or {}).get("default") or []
            for model in models:
                targets.append(CatalogTarget(endpoint=name, model=model))
        return targets

    def resolve(self, endpoint: str, model: str) -> ResolvedTarget | None:
        """The real base_url/api_key for one (endpoint, model) pair, or
        None if it is no longer registered -- the endpoint was removed, or
        that model dropped from its `models.default` list. Treated as
        config drift by the caller, not an error: a Combo can reference a
        target that has since disappeared from librechat.yaml."""
        for endpoint_cfg in self._load_endpoints():
            if endpoint_cfg.get("name") != endpoint:
                continue
            models = (endpoint_cfg.get("models") or {}).get("default") or []
            if model not in models:
                return None
            return ResolvedTarget(
                base_url=endpoint_cfg.get("baseURL", ""),
                api_key=_resolve_env(endpoint_cfg.get("apiKey", "")),
            )
        return None
