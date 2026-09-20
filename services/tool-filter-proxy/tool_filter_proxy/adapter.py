"""Adapts an OpenAI-compatible chat-completion body to the filtering core, and back.

This is the ONLY module that understands the OpenAI wire format or LibreChat's naming.
The filtering core (`tool_filtering`) stays platform-agnostic; the registry
(`tool_registry`) is read-only. Replacing LibreChat with another OpenAI-compatible
front end means changing this file and nothing else.

The safety contract, enforced in `filter_body`:

    0 < len(filtered) <= len(original)      else forward the ORIGINAL body unchanged

Filtering is strictly subtractive - it can only remove entries from the array the caller
already sent, so it can never introduce a tool the agent did not have. It is a
context/performance optimization, never an authorization mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tool_filtering import rank
from tool_filtering.engine import MAX_K

from .librechat_names import split_server


@dataclass(frozen=True)
class ProxyTool:
    """Flattened view of one OpenAI tool entry.

    `name`/`description`/`mcp_server` are what the filtering core reads (it accepts any
    object exposing those attributes). `original` is the caller's untouched dict, which
    is what gets written back into the request - so the outgoing payload is byte-identical
    to what LibreChat sent, minus removed entries.
    """
    name: str
    description: str
    mcp_server: str | None
    original: dict


@dataclass(frozen=True)
class FilterDecision:
    """What happened, for logging. Carries no prompt text or tool descriptions."""
    applied: bool
    reason: str            # filtered | passthrough_no_tools | guard_tripped | error | disabled
    tools_before: int
    tools_after: int
    pinned: int
    fallback_tier: str | None
    filter_ms: float | None


def _content_text(content: Any) -> str:
    """OpenAI message content is either a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def extract_request_text(messages: list) -> str:
    """The most recent user turn drives ranking. Falls back to the last message of any
    role so a tool-loop continuation (where the newest message is a tool result) still
    has something to rank against rather than an empty query."""
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _content_text(msg.get("content"))
            if text.strip():
                return text
    for msg in reversed(messages):
        if isinstance(msg, dict):
            text = _content_text(msg.get("content"))
            if text.strip():
                return text
    return ""


def collect_pinned_names(body: dict) -> set[str]:
    """Tool names that MUST survive filtering.

    Two sources, both correctness-critical rather than relevance-driven:

    1. Prior `assistant.tool_calls` - LibreChat resends the whole tool array on every
       iteration of a multi-hop tool loop, with the history growing each time. A provider
       may reject a request whose history references a function no longer declared in
       `tools`, and dropping it would end the chain mid-flight.
    2. An explicit `tool_choice: {"type":"function","function":{"name":...}}` - filtering
       that away is a guaranteed hard error from the provider.
    """
    pinned: set[str] = set()

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict):
                name = (call.get("function") or {}).get("name")
                if isinstance(name, str) and name:
                    pinned.add(name)

    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        if isinstance(name, str) and name:
            pinned.add(name)

    return pinned


def unwrap_tools(tools: Any) -> list[ProxyTool]:
    """Flatten `[{"type":"function","function":{name,description,parameters}}]`.

    The name is nested, which the filtering core's normalizer cannot see on its own -
    handing it the raw OpenAI list makes it skip every entry and return nothing. Doing
    the unwrap here is exactly why this adapter exists.
    """
    out: list[ProxyTool] = []
    if not isinstance(tools, list):
        return out
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = fn.get("description")
        out.append(ProxyTool(
            name=name,
            description=description if isinstance(description, str) else "",
            mcp_server=split_server(name),
            original=entry,
        ))
    return out


def _apply_pins(ranked: list[ProxyTool], candidates: list[ProxyTool],
                pinned_names: set[str]) -> tuple[list[ProxyTool], int]:
    """Guarantee every pinned tool is present, then bound the total.

    Pinned entries are never trimmed - if a conversation legitimately references more
    than MAX_K tools, correctness beats the size target, so the cap lifts to fit them.
    Only ranked (relevance-selected) entries are trimmed.
    """
    by_name = {t.name: t for t in candidates}
    pins = [by_name[n] for n in sorted(pinned_names) if n in by_name]
    pin_names = {t.name for t in pins}

    rest = [t for t in ranked if t.name not in pin_names]
    cap = max(MAX_K, len(pins))
    return pins + rest[: max(0, cap - len(pins))], len(pins)


def filter_body(body: dict, metadata_provider=None, *, top_k: int = 20,
                enabled: bool = True) -> tuple[dict, FilterDecision]:
    """Return (body_to_forward, decision).

    On any doubt the ORIGINAL body object is returned unchanged - never a partially
    rewritten one, and never `tools: []`.
    """
    original = body.get("tools")

    if not enabled:
        return body, FilterDecision(False, "disabled", len(original or []), len(original or []), 0, None, None)

    if not isinstance(original, list) or not original:
        return body, FilterDecision(False, "passthrough_no_tools", 0, 0, 0, None, None)

    try:
        candidates = unwrap_tools(original)
        if not candidates:
            # Every entry was unusable (unknown shape). Forward untouched rather than
            # sending an empty or arbitrarily-reduced tool array.
            return body, FilterDecision(False, "guard_tripped", len(original), len(original), 0, None, None)

        pinned_names = collect_pinned_names(body)
        request_text = extract_request_text(body.get("messages") or [])

        result = rank(
            request_text,
            {"recent_tool_names": sorted(pinned_names)},
            candidates,
            k=top_k,
            metadata_provider=metadata_provider,
        )
        selected, pin_count = _apply_pins(list(result.tools), candidates, pinned_names)

        # THE GUARD. Subtractive-only and never empty; anything else forwards untouched.
        if not (0 < len(selected) <= len(original)):
            return body, FilterDecision(
                False, "guard_tripped", len(original), len(original), pin_count,
                result.trace.fallback_tier, result.trace.latency_ms,
            )

        new_body = dict(body)
        new_body["tools"] = [t.original for t in selected]
        return new_body, FilterDecision(
            True, "filtered", len(original), len(selected), pin_count,
            result.trace.fallback_tier, result.trace.latency_ms,
        )

    except Exception:  # noqa: BLE001 - availability first: a filtering fault must not
        # break chat. Forward the original request exactly as LibreChat sent it.
        return body, FilterDecision(False, "error", len(original), len(original), 0, None, None)
