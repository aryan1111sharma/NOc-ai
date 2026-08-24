"""HTTP-level behaviour: routing, SSE passthrough, header/auth forwarding, error relay.

Upstream is an httpx MockTransport injected via build_app(client=...), so nothing here
touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import chat_body, openai_tool
from tool_filter_proxy.app import build_app
from tool_filter_proxy.config import Settings
from tool_filter_proxy.metadata import RegistryMetadata

SETTINGS = Settings(
    upstreams={"kimi": "https://api.kimi.test/coding/v1", "glm": "https://api.glm.test/v4"},
)


def _client(handler, settings: Settings = SETTINGS, metadata=None) -> TestClient:
    transport = httpx.MockTransport(handler)
    upstream = httpx.AsyncClient(transport=transport)
    app = build_app(settings, metadata=metadata or RegistryMetadata({}), client=upstream)
    return TestClient(app)


def _captured_handler(captured: dict, *, status=200, body=b'{"ok":true}',
                       headers=None):
    """Records what reached the upstream and replies.

    Uses `stream=ByteStream(...)` rather than `content=...`: a Response built with
    `content` arrives already stream-consumed, which a real transport under
    `send(stream=True)` never does. Modelling it as a stream keeps the mock faithful to
    production behaviour for both streaming and non-streaming replies.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers or {})
    return handler


# ── routing ──────────────────────────────────────────────────────────────────────────────────

def test_routes_prefix_to_the_configured_upstream(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        r = c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    assert r.status_code == 200
    assert cap["url"] == "https://api.kimi.test/coding/v1/chat/completions"


def test_second_provider_prefix_routes_independently(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/glm/chat/completions", json=chat_body(noc_tools))
    assert cap["url"] == "https://api.glm.test/v4/chat/completions"


def test_unknown_prefix_returns_404_without_calling_upstream():
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return httpx.Response(200)

    with _client(handler) as c:
        r = c.post("/nope/chat/completions", json={"model": "m"})
    assert r.status_code == 404
    assert called["n"] == 0
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_query_params_are_forwarded():
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.get("/kimi/models?limit=5")
    assert "limit=5" in cap["url"]


def test_non_chat_paths_pass_through_untouched():
    cap = {}
    with _client(_captured_handler(cap)) as c:
        r = c.get("/kimi/models")
    assert r.status_code == 200
    assert cap["url"] == "https://api.kimi.test/coding/v1/models"


def test_healthz_is_served_locally():
    with _client(_captured_handler({})) as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert sorted(r.json()["upstreams"]) == ["glm", "kimi"]


# ── filtering applied on the wire ────────────────────────────────────────────────────────────

def test_tools_are_reduced_in_the_forwarded_body(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    sent = json.loads(cap["body"])
    assert 0 < len(sent["tools"]) < len(noc_tools)
    original = {t["function"]["name"] for t in noc_tools}
    assert {t["function"]["name"] for t in sent["tools"]} <= original


def test_messages_and_params_reach_upstream_unchanged(noc_tools):
    cap = {}
    body = chat_body(noc_tools, stream=False, temperature=0.42)
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=body)
    sent = json.loads(cap["body"])
    assert sent["messages"] == body["messages"]
    assert sent["temperature"] == 0.42
    assert sent["model"] == body["model"]


def test_request_without_tools_is_forwarded_byte_identical():
    """Title-generation requests (titleConvo: true) take this path."""
    cap = {}
    body = {"model": "kimi-for-coding-highspeed",
            "messages": [{"role": "user", "content": "Write a short title"}]}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=body)
    assert json.loads(cap["body"]) == body


def test_non_json_body_is_forwarded_verbatim():
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", content=b"not json at all",
               headers={"content-type": "application/json"})
    assert cap["body"] == b"not json at all"


def test_passthrough_only_mode_disables_filtering(noc_tools):
    cap = {}
    settings = Settings(upstreams=SETTINGS.upstreams, passthrough_only=True)
    with _client(_captured_handler(cap), settings=settings) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    assert len(json.loads(cap["body"])["tools"]) == len(noc_tools)


# ── headers / auth ───────────────────────────────────────────────────────────────────────────

def test_authorization_header_is_forwarded(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools),
               headers={"Authorization": "Bearer sk-test-secret"})
    assert cap["headers"]["authorization"] == "Bearer sk-test-secret"


def test_stale_content_length_is_not_forwarded(noc_tools):
    """The body shrinks when tools are removed; forwarding the original length would
    truncate or corrupt the request."""
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    sent_len = cap["headers"].get("content-length")
    assert sent_len is None or int(sent_len) == len(cap["body"])


def test_hop_by_hop_request_headers_are_stripped(noc_tools):
    cap = {}
    with _client(_captured_handler(cap)) as c:
        c.post("/kimi/chat/completions", json=chat_body(noc_tools),
               headers={"Connection": "keep-alive", "TE": "trailers"})
    assert "te" not in cap["headers"]


def test_response_headers_are_relayed():
    handler = _captured_handler({}, headers={"x-provider-request-id": "abc123"})
    with _client(handler) as c:
        r = c.post("/kimi/chat/completions", json={"model": "m"})
    assert r.headers["x-provider-request-id"] == "abc123"


# ── streaming ────────────────────────────────────────────────────────────────────────────────

_SSE_CHUNKS = [
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    b"data: [DONE]\n\n",
]


def test_sse_stream_is_relayed_intact(noc_tools):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(b"".join(_SSE_CHUNKS)),
        )

    with _client(handler) as c:
        with c.stream("POST", "/kimi/chat/completions",
                      json=chat_body(noc_tools, stream=True)) as r:
            assert r.status_code == 200
            assert r.headers["content-type"] == "text/event-stream"
            received = b"".join(r.iter_raw())
    assert received == b"".join(_SSE_CHUNKS)
    assert b"[DONE]" in received


def test_streaming_tool_call_response_passes_through(noc_tools):
    """tool_calls come back referencing LibreChat's mangled names; the proxy never
    rewrites names, so they resolve on the way back with no mapping needed."""
    chunk = (b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
             b'"function":{"name":"charger_get_charging_status_mcp_noc","arguments":"{}"}}]}}]}\n\n')

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=httpx.ByteStream(chunk + b"data: [DONE]\n\n"))

    with _client(handler) as c:
        with c.stream("POST", "/kimi/chat/completions",
                      json=chat_body(noc_tools, stream=True)) as r:
            body = b"".join(r.iter_raw())
    assert b"charger_get_charging_status_mcp_noc" in body


# ── error / failure relay ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_provider_error_status_and_body_are_relayed(status, noc_tools):
    payload = json.dumps({"error": {"message": "upstream said no", "code": status}}).encode()

    def handler(request):
        return httpx.Response(status, stream=httpx.ByteStream(payload),
                              headers={"content-type": "application/json"})

    with _client(handler) as c:
        r = c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    assert r.status_code == status
    assert r.json()["error"]["message"] == "upstream said no"


def test_upstream_timeout_becomes_504():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with _client(handler) as c:
        r = c.post("/kimi/chat/completions", json={"model": "m"})
    assert r.status_code == 504
    assert r.json()["error"]["type"] == "timeout"


def test_upstream_connect_error_becomes_502():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with _client(handler) as c:
        r = c.post("/kimi/chat/completions", json={"model": "m"})
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "bad_gateway"


def test_filtering_failure_still_reaches_upstream(monkeypatch, noc_tools):
    """Availability: a filtering fault must forward the original request, not 500."""
    monkeypatch.setattr("tool_filter_proxy.adapter.rank",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    cap = {}
    with _client(_captured_handler(cap)) as c:
        r = c.post("/kimi/chat/completions", json=chat_body(noc_tools))
    assert r.status_code == 200
    assert len(json.loads(cap["body"])["tools"]) == len(noc_tools)  # unfiltered, intact
