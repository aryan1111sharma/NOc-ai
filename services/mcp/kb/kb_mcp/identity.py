"""Caller identity from MCP request headers.

Pure by design: no network, no framework, no config. This is the module that
decides who may write to the knowledge base, so it must be exhaustively
testable without standing anything up.

LibreChat substitutes {{LIBRECHAT_USER_ID}}, {{LIBRECHAT_USER_EMAIL}} and
{{LIBRECHAT_USER_ROLE}} into these headers server-side, from the authenticated
session (see @librechat/api/src/utils/env.ts). The model never sees them and
has no channel to set them — which is the whole reason a chat-facing tool may
be trusted to write. Values that are not header-safe ASCII arrive base64-encoded
behind a `b64:` prefix, which is documented in that same module.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

USER_ID_HEADER = "x-user-id"
USER_EMAIL_HEADER = "x-user-email"
USER_ROLE_HEADER = "x-user-role"


class IdentityError(ValueError):
    """The request carries no usable caller identity."""


@dataclass(frozen=True)
class Caller:
    user_id: str
    email: str
    role: str


def _decode(value: str) -> str:
    if not value.startswith("b64:"):
        return value
    try:
        return base64.b64decode(value[4:], validate=True).decode("utf-8")
    except Exception:
        # A malformed value is not a partial identity — return nothing rather
        # than a mangled string that might match something downstream.
        return ""


def parse_caller(headers: dict[str, str]) -> Caller:
    lowered = {str(k).lower(): _decode(str(v or "").strip()).strip()
               for k, v in (headers or {}).items()}
    user_id = lowered.get(USER_ID_HEADER, "")
    if not user_id:
        raise IdentityError(
            "This request carries no caller identity. The KB server requires the "
            "X-User-Id header that LibreChat injects; without it the server cannot "
            "tell who is asking, and refuses rather than assuming. If you are seeing "
            "this in chat, the `kb` MCP server is missing its `headers:` block in "
            "librechat.yaml."
        )
    return Caller(
        user_id=user_id,
        email=lowered.get(USER_EMAIL_HEADER, ""),
        role=lowered.get(USER_ROLE_HEADER, ""),
    )


def is_contributor(caller: Caller, allowed_roles: tuple[str, ...]) -> bool:
    allowed = {r.casefold() for r in allowed_roles if r and r.strip()}
    role = caller.role.casefold().strip()
    return bool(role) and role in allowed
