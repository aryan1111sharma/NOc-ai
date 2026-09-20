"""Merges system-defined metadata with admin-managed configuration into one
read-only record per tool - the single view Policy/Agents are meant to query.

Neither `metadata.load_metadata()` nor `config_store.YamlToolConfigStore` is meant to
be consumed directly by a Policy engine or an agent; `load_registry()` is the
supported entry point for this package.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config_store import CONFIG_SCHEMA_PATH, ConfigError, DEFAULT_CONFIG_PATH, YamlToolConfigStore
from .metadata import DEFAULT_METADATA_DIR, METADATA_SCHEMA_PATH, MetadataError, load_metadata

log = logging.getLogger(__name__)

# Used only if a tool somehow has metadata but no config row yet (should not happen
# once the registry has been built/seeded - see registry/README.md). The loader
# does not fail the whole registry load for one missing row; it falls back to this
# and logs a warning so the gap is visible rather than silently masked.
_DEFAULT_APPROVAL_BY_RISK = {"LOW": False, "MEDIUM": False, "HIGH": True, "CRITICAL": True}


@dataclass(frozen=True)
class ToolRecord:
    tool_name: str
    mcp_server: str
    domain: str
    description: str
    capability: str        # system-defined - only ever comes from metadata
    risk: str               # system-defined - only ever comes from metadata
    enabled: bool           # admin-managed - only ever comes from config
    approval_required: bool  # admin-managed - only ever comes from config


class RegistryError(ValueError):
    """The registry could not be loaded (metadata error, config error, or an
    orphaned config entry with no matching metadata)."""


def load_registry(metadata_dir: Path | str = DEFAULT_METADATA_DIR,
                   metadata_schema_path: Path | str = METADATA_SCHEMA_PATH,
                   config_path: Path | str = DEFAULT_CONFIG_PATH,
                   config_schema_path: Path | str = CONFIG_SCHEMA_PATH) -> dict[str, ToolRecord]:
    """Load and merge the registry. Raises RegistryError if either store is
    malformed, or if the config store references a tool_name with no metadata."""
    try:
        metadata = load_metadata(metadata_dir, metadata_schema_path)
    except MetadataError as exc:
        raise RegistryError(f"metadata load failed: {exc}") from exc

    try:
        store = YamlToolConfigStore(config_path, config_schema_path)
    except ConfigError as exc:
        raise RegistryError(f"config load failed: {exc}") from exc

    configs = store.all()

    orphan_config = sorted(set(configs) - set(metadata))
    if orphan_config:
        raise RegistryError(
            f"config entries reference unknown tool_name(s) with no metadata: {orphan_config}"
        )

    records: dict[str, ToolRecord] = {}
    missing_config = []
    for name, meta in metadata.items():
        cfg = configs.get(name)
        if cfg is None:
            missing_config.append(name)
            enabled, approval_required = True, _DEFAULT_APPROVAL_BY_RISK[meta.risk]
        else:
            enabled, approval_required = cfg.enabled, cfg.approval_required

        records[name] = ToolRecord(
            tool_name=meta.tool_name, mcp_server=meta.mcp_server, domain=meta.domain,
            description=meta.description, capability=meta.capability, risk=meta.risk,
            enabled=enabled, approval_required=approval_required,
        )

    if missing_config:
        log.warning(
            "%d tool(s) have metadata but no config entry; applying risk-based "
            "defaults: %s", len(missing_config), missing_config,
        )

    return records
