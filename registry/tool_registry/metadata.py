"""Loads and validates system-defined tool metadata from registry/metadata/*.yaml.

Immutable by construction: this module only ever reads registry/metadata/, never
registry/config/, and validates against a schema (tool_metadata.schema.json) that has
no `enabled`/`approval_required` property - so nothing loaded here can be, or reflect,
an admin-configured value. Capability/risk changes are only supposed to happen by
editing one of these YAML files (a reviewed, git-tracked change), never at runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover - jsonschema is a declared dependency; this
    jsonschema = None  # just keeps the module importable if it's ever missing.

_REGISTRY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METADATA_DIR = _REGISTRY_ROOT / "metadata"
METADATA_SCHEMA_PATH = _REGISTRY_ROOT / "schema" / "tool_metadata.schema.json"

VALID_CAPABILITIES = ("READ", "CONTROL", "WRITE", "DESTRUCTIVE")
VALID_RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class MetadataError(ValueError):
    """The metadata store is malformed or violates a registry invariant (duplicate
    tool_name, unknown capability/risk, schema violation)."""


@dataclass(frozen=True)
class ToolMetadata:
    tool_name: str
    mcp_server: str
    domain: str
    description: str
    capability: str
    risk: str


def _load_schema(schema_path: Path):
    if not schema_path.is_file():
        return None
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_metadata(metadata_dir: Path | str = DEFAULT_METADATA_DIR,
                   schema_path: Path | str = METADATA_SCHEMA_PATH) -> dict[str, ToolMetadata]:
    """Load every registry/metadata/<server>.yaml file and return {tool_name: ToolMetadata}.

    Raises MetadataError on: a missing directory, malformed YAML, a schema violation
    (this is what catches an `enabled`/`approval_required` field that shouldn't be
    here), a tool_name duplicated across files, or a capability/risk value outside
    the known enums.
    """
    metadata_dir = Path(metadata_dir)
    schema = _load_schema(Path(schema_path))
    if not metadata_dir.is_dir():
        raise MetadataError(f"metadata directory not found: {metadata_dir}")

    out: dict[str, ToolMetadata] = {}
    for path in sorted(metadata_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise MetadataError(f"{path}: not valid YAML: {exc}") from exc

        if not isinstance(raw, dict):
            raise MetadataError(f"{path}: top level must be a mapping")

        if schema is not None and jsonschema is not None:
            try:
                jsonschema.validate(raw, schema)
            except jsonschema.ValidationError as exc:
                raise MetadataError(f"{path}: schema violation at {list(exc.path)}: {exc.message}") from exc

        server = raw.get("mcp_server")
        for entry in raw.get("tools", []):
            name = entry["tool_name"]
            if name in out:
                raise MetadataError(
                    f"{path}: tool_name {name!r} is already defined for server "
                    f"{out[name].mcp_server!r} - tool names must be unique across the whole registry"
                )
            if entry["capability"] not in VALID_CAPABILITIES:
                raise MetadataError(f"{path}: {name!r} has invalid capability {entry['capability']!r}")
            if entry["risk"] not in VALID_RISKS:
                raise MetadataError(f"{path}: {name!r} has invalid risk {entry['risk']!r}")

            out[name] = ToolMetadata(
                tool_name=name,
                mcp_server=server,
                domain=entry["domain"],
                description=entry["description"],
                capability=entry["capability"],
                risk=entry["risk"],
            )
    return out
