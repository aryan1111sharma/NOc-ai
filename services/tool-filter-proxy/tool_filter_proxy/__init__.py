"""OpenAI-compatible tool-filtering proxy.

Sits between LibreChat and the model provider, shrinking the `tools[]` array on each
chat-completion request via the platform-agnostic filtering core, and relaying
everything else (streaming, headers, status codes, errors) unchanged.

See ../README.md and the repo-root FAST_TOOL_FILTERING_DESIGN.md.
"""

from .adapter import FilterDecision, ProxyTool, filter_body
from .app import build_app
from .config import Settings, settings
from .metadata import RegistryMetadata

__all__ = [
    "build_app", "filter_body", "FilterDecision", "ProxyTool",
    "RegistryMetadata", "Settings", "settings",
]
