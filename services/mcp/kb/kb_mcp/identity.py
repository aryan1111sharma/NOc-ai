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

The role is deliberately never base64-decoded, even though user id and email
are. Role names (ADMIN, kb-contributor, noc-engineer, ...) are always
header-safe ASCII, so LibreChat's env.ts will never emit a `b64:`-prefixed
role — a decoding path for it could not legitimately execute. Decoding it
anyway would not cross a new trust boundary (anyone able to inject
`x-user-role: b64:...` could equally inject `x-user-role: ADMIN` directly),
but it would leave a second, less obvious way to spell a privileged role —
exactly the kind of thing that survives a refactor and outlives the reasoning
that made it safe. Narrowing what this boundary accepts is worth doing even
at zero measured risk.

Known behaviour, established by adversarial testing rather than assumed:

- A header dict containing the same name in two cases resolves to whichever
  was inserted last. Unreachable through a real HTTP stack, which coalesces
  repeated headers before this module sees them.
- A value that legitimately begins with the literal text "b64:" decodes to
  nothing rather than passing through. Only the email can plausibly hit this,
  where it degrades an audit field and grants nothing.
- Casefold does not create a bypass: Turkish dotless "admın" does not
  match "ADMIN".
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
    lowered = {str(k).lower(): str(v or "").strip() for k, v in (headers or {}).items()}

    # The role is taken verbatim, never base64-decoded. Role names are always
    # header-safe ASCII, so LibreChat never encodes one — and a decoding path
    # that cannot legitimately run is a second way to spell a privileged role
    # that would outlive the reasoning which made it safe. Email and user id
    # keep decoding: those can carry non-ASCII.
    user_id = _decode(lowered.get(USER_ID_HEADER, "")).strip()
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
        email=_decode(lowered.get(USER_EMAIL_HEADER, "")).strip(),
        role=lowered.get(USER_ROLE_HEADER, ""),
    )


def is_contributor(caller: Caller, allowed_roles: tuple[str, ...]) -> bool:
    allowed = {r.casefold() for r in allowed_roles if r and r.strip()}
    role = caller.role.casefold().strip()
    return bool(role) and role in allowed
