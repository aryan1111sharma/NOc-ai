"""The single outbound-GET choke point for NOC *backend* reads.

Why this module exists
----------------------
The generic backend GET helper used to live in ``reports.py`` -- a feature module -- and was
imported from there by ``fleet``, ``control_center`` and ``history``. Infrastructure hiding in
a feature module is how two spellings of "GET the backend" came to differ in *security*
behaviour: every caller but one passed ``params=``, and the one that did not
(``history._fetch_cached_ocpp_config``) let a ``?method=POST`` smuggled inside a ``charger_id``
survive as a **real** query parameter -- the parameter the NOC charger-proxy route uses to pick
the HTTP verb it forwards to live high-voltage hardware. The other callers were safe only by
luck: their ``params={...}`` happened to overwrite the injected query string.

The three properties below exist so that failure mode is unrepresentable, not merely absent:

1. **``params`` is always sent.** It defaults to ``{}`` and is passed to httpx on every call,
   so a query string smuggled into a path can never survive as real query parameters.
2. **Model-controlled path components are validated, by name.** A caller does not interpolate
   an identifier; it hands over a *template* plus typed identifier keywords
   (``charger_id=``/``session_id=``), which are validated with ``noc_mcp.identifiers`` -- the
   same validators ``control_center`` and ``ssh_exec`` use -- **before** any socket is touched.
   Rejection is fail-closed and fail-loud: zero outbound requests, a structured error back.
3. **The assembled URL is re-validated regardless of how it was built.** ``_SAFE_URL`` admits
   only RFC 3986 unreserved characters and ``/``, and dot segments are rejected. This is the
   backstop that catches a caller who hand-splices an f-string anyway: both confirmed
   exploits (``...?method=POST&x=`` and ``../../../api/v1/admin/users``) are rejected here on
   the assembled string alone, even with the typed keywords unused.

Never-raise contract: like ``control_center._proxy_get``, every failure -- rejected input,
transport error, non-2xx, non-JSON body -- comes back as ``{"error": ..., "url": ...}`` so
composites degrade to partial results instead of failing a whole tool call.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

from urllib.parse import quote

import httpx

from .identifiers import validate_charger_id, validate_session_id

# Identifier keyword -> validator. A caller may only fill placeholders named here; anything
# else is a programming error and is rejected rather than sent.
_VALIDATORS = {
    "charger_id": validate_charger_id,
    "session_id": validate_session_id,
}

# Absolute path made only of RFC 3986 *unreserved* characters plus the '/' separators. No
# '?', '#', '%', '\', ':', '=', '&' or whitespace -- none of which can appear in a legitimate
# NOC backend URL built from this repo's constants, and every one of which changes how a
# decoding receiver parses the request.
#
# \A..\Z, not ^..$: Python's '$' also matches before a trailing newline, which would let a
# request-splitting character through the one check that is meant to hold whatever built the
# URL. Same reasoning as identifiers._CHARGER_ID_ALLOWED.
_SAFE_URL = re.compile(r"\A/[A-Za-z0-9._~/-]*\Z")


def build_url(template: str, **ids: Any) -> Tuple[Optional[str], Optional[str]]:
    """Fill ``template``'s identifier placeholders, or explain why the result is unsafe.

    Returns ``(url, None)`` on success or ``(None, reason)`` on rejection, mirroring
    ``control_center._validate_proxy_url`` so both choke points answer in one shape.

    ``template`` is an absolute backend path, optionally carrying ``{charger_id}`` and/or
    ``{session_id}``. Each supplied keyword is validated with its own validator and then
    percent-encoded. As on the charger proxy, the encoding is a *backstop*, not the defence:
    the receiver decodes before it routes, so validation is what actually closes the hole --
    under the current allowlists ``quote`` is a no-op, which is exactly the point.
    """
    if not isinstance(template, str) or not template.startswith("/"):
        return None, f"ValueError: url must be an absolute backend path (got {template!r})"

    url = template
    for name, value in ids.items():
        validator = _VALIDATORS.get(name)
        if validator is None:
            return None, f"ValueError: no validator registered for {name!r}"
        placeholder = "{" + name + "}"
        if placeholder not in url:
            return None, f"ValueError: url has no {placeholder} to fill ({template!r})"
        reason = validator(value)
        if reason is not None:
            return None, reason
        url = url.replace(placeholder, quote(value, safe=""))

    if "{" in url or "}" in url:
        return None, f"ValueError: url has an unfilled placeholder ({url!r})"
    if not _SAFE_URL.match(url):
        return None, f"ValueError: url has an illegal character ({url!r})"
    segments = url.split("/")[1:]
    for seg in segments:
        if seg in ("", ".", ".."):
            # '' rejects '//' and a trailing slash; '.'/'..' would be normalised away by a
            # decoding receiver and shift every following segment.
            return None, f"ValueError: url segment {seg!r} is not allowed ({url!r})"
    return url, None


async def get(client, url: str, params: Optional[Mapping[str, Any]] = None, **ids: Any) -> dict:
    """GET a NOC backend URL, returning a structured error rather than raising.

    ``url`` is a template; model-controlled path components are passed as ``charger_id=`` /
    ``session_id=`` keywords and validated before any request is made. ``params`` is always
    sent (``{}`` when unset), so a query string cannot be smuggled in through a path.

    A non-2xx response additionally carries ``status`` and the decoded ``body``, so a caller
    that must distinguish a semantic 404 (e.g. ``code=NO_CACHE``) from a transport failure can
    do so without reaching for the raw response.
    """
    built, reason = build_url(url, **ids)
    if built is None:
        return {"error": reason, "url": url}
    try:
        resp = await client.get(built, params=dict(params or {}))
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except httpx.HTTPStatusError as e:
        return {"error": f"{type(e).__name__}: {e}", "url": built,
                "status": e.response.status_code, "body": _body(e.response)}
    except Exception as e:  # noqa: BLE001 - surface any failure as a clean payload
        return {"error": f"{type(e).__name__}: {e}", "url": built}


def _body(resp: httpx.Response) -> Any:
    """The response body as JSON, or ``None`` when it is not JSON at all."""
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is simply absent, not a failure
        return None
