"""Tool Registry access layer.

Two physically separate stores (see ../TOOL_REGISTRY_DESIGN.md):
  - registry/metadata/*.yaml  - system-defined, immutable at runtime (this package
    never writes to it)
  - registry/config/tool_config.yaml - admin-managed (enabled, approval_required)

Public API:
    load_registry()        -> dict[tool_name, ToolRecord]   merged view for Policy/Agents
    load_metadata()         -> dict[tool_name, ToolMetadata]  metadata only
    YamlToolConfigStore      admin config get/set/all

Nothing in this package knows about LibreChat, agents, or a policy engine - it only
loads and merges tool facts and tool policy-state.
"""
from .config_store import ConfigError, ToolConfig, YamlToolConfigStore
from .loader import RegistryError, ToolRecord, load_registry
from .metadata import MetadataError, ToolMetadata, VALID_CAPABILITIES, VALID_RISKS, load_metadata

__all__ = [
    "load_registry", "ToolRecord", "RegistryError",
    "load_metadata", "ToolMetadata", "MetadataError", "VALID_CAPABILITIES", "VALID_RISKS",
    "YamlToolConfigStore", "ToolConfig", "ConfigError",
]
