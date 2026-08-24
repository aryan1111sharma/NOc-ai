"""Admin-managed tool configuration: enabled / approval_required.

This is the ONLY store a future Admin UI should ever write to. Its schema
(registry/schema/tool_config.schema.json) has no `capability`/`risk` property, so
nothing written through this interface can alter a tool's system-defined
classification - that lives in registry/metadata (see metadata.py), which this
module never reads or writes.

`YamlToolConfigStore` is the v1 implementation, backed by one YAML file. A future
database-backed implementation (e.g. a `tool_config` table) should expose the
identical get/set/all interface so the loader, a Policy engine, and an Admin UI
never need to change when the storage engine does - see TOOL_REGISTRY_DESIGN.md §2.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

_REGISTRY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _REGISTRY_ROOT / "config" / "tool_config.yaml"
CONFIG_SCHEMA_PATH = _REGISTRY_ROOT / "schema" / "tool_config.schema.json"


class ConfigError(ValueError):
    """The config store is malformed, or a caller asked for/set a tool_name it
    doesn't hold."""


@dataclass(frozen=True)
class ToolConfig:
    tool_name: str
    enabled: bool
    approval_required: bool
    updated_by: str | None = None
    updated_at: str | None = None


def _load_schema(schema_path: Path):
    if not schema_path.is_file():
        return None
    return json.loads(schema_path.read_text(encoding="utf-8"))


class YamlToolConfigStore:
    """get/set/all over registry/config/tool_config.yaml.

    This is the interface a future Policy engine, Admin UI, and this package's own
    loader all depend on - swap this class for a DB-backed one later without
    touching any of those callers.
    """

    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH,
                 schema_path: Path | str = CONFIG_SCHEMA_PATH):
        self._path = Path(path)
        self._schema = _load_schema(Path(schema_path))
        self._data = self._read()

    def _read(self) -> dict:
        if not self._path.is_file():
            raise ConfigError(f"config file not found: {self._path}")
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{self._path}: not valid YAML: {exc}") from exc

        if not isinstance(raw, dict):
            raise ConfigError(f"{self._path}: top level must be a mapping")

        self._validate(raw)
        return raw

    def _validate(self, data: dict) -> None:
        if self._schema is not None and jsonschema is not None:
            try:
                jsonschema.validate(data, self._schema)
            except jsonschema.ValidationError as exc:
                raise ConfigError(f"{self._path}: schema violation at {list(exc.path)}: {exc.message}") from exc

    def all(self) -> dict[str, ToolConfig]:
        return {
            name: self._to_config(name, entry)
            for name, entry in self._data.get("tools", {}).items()
        }

    def get(self, tool_name: str) -> ToolConfig:
        entry = self._data.get("tools", {}).get(tool_name)
        if entry is None:
            raise ConfigError(f"no config entry for tool_name {tool_name!r}")
        return self._to_config(tool_name, entry)

    def set(self, tool_name: str, *, enabled: bool | None = None,
            approval_required: bool | None = None, actor: str) -> ToolConfig:
        """Update one tool's admin-managed fields.

        There is deliberately no `capability=` / `risk=` parameter - not merely
        unvalidated, but structurally absent from this method's signature, matching
        the schema's `additionalProperties: false` on the same two fields.
        """
        tools = self._data.setdefault("tools", {})
        if tool_name not in tools:
            raise ConfigError(f"no config entry for tool_name {tool_name!r}")
        if enabled is None and approval_required is None:
            raise ConfigError("set() requires at least one of enabled= / approval_required=")
        if not actor:
            raise ConfigError("set() requires actor= for the audit trail")

        entry = dict(tools[tool_name])
        if enabled is not None:
            entry["enabled"] = bool(enabled)
        if approval_required is not None:
            entry["approval_required"] = bool(approval_required)
        entry["updated_by"] = actor
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()

        candidate = dict(self._data)
        candidate["tools"] = {**tools, tool_name: entry}
        self._validate(candidate)  # reject before persisting, not after

        self._data = candidate
        self._write()
        return self.get(tool_name)

    def _write(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, sort_keys=False, allow_unicode=True,
                            default_flow_style=False, width=100)

    @staticmethod
    def _to_config(tool_name: str, entry: dict) -> ToolConfig:
        return ToolConfig(
            tool_name=tool_name,
            enabled=bool(entry["enabled"]),
            approval_required=bool(entry["approval_required"]),
            updated_by=entry.get("updated_by"),
            updated_at=entry.get("updated_at"),
        )
