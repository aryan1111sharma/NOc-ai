"""Fast Deterministic Tool Filtering.

Sits between the agent's available-tool set and the LLM. See
../FAST_TOOL_FILTERING_DESIGN.md for the design; this package is its implementation.

Public API:
    rank(request_text, session_context, allowed_tools, *, k=20, use_cache=True,
          metadata_provider=None) -> FilterResult

`allowed_tools` is whatever set of tools is currently available to the active agent -
today that comes from LibreChat/MCP; when Policy/Agent Access goes live it will come
from there instead. Either way this package treats it as the input boundary: it never
reads the Tool Registry or a Policy store, and never returns a tool outside what it was
given. It is a performance/context optimization only, never a permission or security
layer - filtering can reduce what the LLM sees, never expand it.
"""
from .engine import DEFAULT_K, MAX_K, MIN_K, FilterResult, FilterTrace, rank
from .index import clear_default_cache
from .normalize import NormalizationError, NormalizedTool, normalize_tool, normalize_tools

__all__ = [
    "rank", "FilterResult", "FilterTrace", "DEFAULT_K", "MIN_K", "MAX_K",
    "clear_default_cache", "NormalizedTool", "NormalizationError",
    "normalize_tool", "normalize_tools",
]
