"""Persistent Agent -> Combo mapping.

Deliberately a JSON file on disk rather than a MongoDB collection: this repo's
own precedent for admin-managed, low-write-volume configuration is
knowledge/kb-registry.json (see services/mcp/kb/kb_mcp/registry.py) -- a
single versioned file, atomically rewritten, with no separate database
process. Following it here means one fewer piece of infrastructure and one
fewer schema to keep in sync with LibreChat's own Mongo models.

A Combo's targets are references only: an `{endpoint, model}` pair naming an
entry in llm_router.catalog.Catalog, which is itself sourced from
librechat.yaml. Nothing in this file is a secret -- there is no credential
for a Combo to expose, by construction.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: An agent_id is LibreChat's own Agent.id (e.g. "agent_kimi26NOCEngineerV1")
#: and becomes a route segment in the admin API and the OpenAI `model`
#: string a client sends, so it must be safe in a URL path and free of
#: characters that would need escaping in either place.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

REGISTRY_VERSION = 2


class RegistryError(ValueError):
    """A registry record is malformed or a mutation was refused.

    Raised for unsafe identifiers, targets that don't parse, and mutations
    that would leave the registry inconsistent. Messages are written to be
    shown to the admin verbatim.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_name(name: str) -> None:
    if not name or not SAFE_NAME.match(name):
        raise RegistryError(
            f"invalid id {name!r}: only letters, digits, '-' and '_' are allowed"
        )


@dataclass(frozen=True)
class ComboTarget:
    """One priority-ordered entry in an agent's fallback Combo: a reference
    to an (endpoint, model) pair in the Catalog, plus whether it is
    currently in play. The same endpoint can appear more than once with a
    different model -- that is the whole point of separating the two."""

    endpoint: str
    model: str
    enabled: bool = True

    def to_json(self) -> dict:
        return {"endpoint": self.endpoint, "model": self.model, "enabled": self.enabled}

    @staticmethod
    def from_json(data: dict) -> "ComboTarget":
        return ComboTarget(
            endpoint=data["endpoint"],
            model=data["model"],
            enabled=data.get("enabled", True),
        )


@dataclass(frozen=True)
class Combo:
    """An agent's ordered provider fallback list: index 0 is tried first."""

    agent_id: str
    targets: tuple[ComboTarget, ...]
    enabled: bool = True
    #: Agent.provider/model as they were before this Combo was first
    #: enabled, so the LibreChat-side facade can restore them if the Combo
    #: is disabled or deleted. Opaque to this service: it never reads these
    #: itself, only stores and returns them.
    original_provider: str | None = None
    original_model: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    #: The target that actually answered the most recent request ("Kimi/k3-256k"),
    #: the full priority-ordered list of targets that were tried to get there, and
    #: when -- set by Registry.record_dispatch after each successful dispatch, so
    #: an admin can see which fallback actually fired without a separate log
    #: search. None/() until the first dispatch. Never touched by upsert_combo's
    #: own target/enabled edits -- see record_dispatch's docstring.
    last_target: str | None = None
    last_attempted: tuple[str, ...] = ()
    last_dispatched_at: str | None = None

    def to_json(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "targets": [t.to_json() for t in self.targets],
            "enabled": self.enabled,
            "original_provider": self.original_provider,
            "original_model": self.original_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_target": self.last_target,
            "last_attempted": list(self.last_attempted),
            "last_dispatched_at": self.last_dispatched_at,
        }

    @staticmethod
    def from_json(data: dict) -> "Combo":
        return Combo(
            agent_id=data["agent_id"],
            targets=tuple(ComboTarget.from_json(t) for t in data.get("targets", [])),
            enabled=data.get("enabled", True),
            original_provider=data.get("original_provider"),
            original_model=data.get("original_model"),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
            last_target=data.get("last_target"),
            last_attempted=tuple(data.get("last_attempted", [])),
            last_dispatched_at=data.get("last_dispatched_at"),
        )


@dataclass(frozen=True)
class UsageStats:
    """Locally observed traffic through this router for one (endpoint,
    model) pair -- never a provider-reported figure. Counts every dispatch
    attempt against this target, whether or not it ultimately won: a target
    that keeps failing is exactly what an admin needs to see here.

    Token counts are cumulative sums of whatever `usage` object a
    non-streaming provider response actually included (see
    llm_router.proxy._extract_usage_tokens) -- never estimated, and never
    populated for a streaming response, which this router does not inspect.
    """

    endpoint: str
    model: str
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    #: HTTP status code (as a string, e.g. "429", "500") or "timeout" /
    #: "connection_error" for the most recent non-2xx outcome.
    last_failure_class: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_used_at: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_json(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "request_count": self.request_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_failure_class": self.last_failure_class,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_used_at": self.last_used_at,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    @staticmethod
    def from_json(data: dict) -> "UsageStats":
        return UsageStats(
            endpoint=data["endpoint"],
            model=data["model"],
            request_count=data.get("request_count", 0),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            last_failure_class=data.get("last_failure_class"),
            last_success_at=data.get("last_success_at"),
            last_failure_at=data.get("last_failure_at"),
            last_used_at=data.get("last_used_at"),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
        )


class Registry:
    """Loads/saves the whole file on every call.

    Simple rather than clever: this is admin-driven CRUD (build a combo,
    reorder its targets) at human speed, not a request-path hot path. A
    process-wide lock is enough to keep concurrent admin edits from racing
    each other; a single instance is what every deployment of this service
    runs.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = threading.Lock()
        if not self._path.exists():
            self._save({"combos": [], "usage": []})

    # -- persistence -------------------------------------------------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"combos": [], "usage": []}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {"combos": raw.get("combos", []), "usage": raw.get("usage", [])}

    def _save(self, data: dict) -> None:
        """Atomic write: tempfile in the same directory, then os.replace.

        A crash mid-write must never leave a truncated/partial registry file
        behind -- os.replace is atomic on both POSIX and Windows, a plain
        write() is not.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "$comment": (
                "NOC LLM Router: per-agent fallback Combos. Managed by the LLM "
                "Routing admin page; hand-edit only if that page is unavailable. "
                "Targets are references (endpoint name + model) into "
                "librechat.yaml -- this file holds no secrets."
            ),
            "version": REGISTRY_VERSION,
            **data,
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".llm-router-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # -- combos ----------------------------------------------------------

    def list_combos(self) -> list[Combo]:
        return [Combo.from_json(c) for c in self._load()["combos"]]

    def get_combo(self, agent_id: str) -> Combo | None:
        for c in self.list_combos():
            if c.agent_id == agent_id:
                return c
        return None

    def upsert_combo(
        self,
        *,
        agent_id: str,
        targets: list[dict],
        enabled: bool = True,
        original_provider: str | None = None,
        original_model: str | None = None,
    ) -> Combo:
        """Create or update. Target catalog membership is validated by the
        caller (llm_router.app), which has the Catalog -- this method only
        enforces shape."""
        _check_name(agent_id)
        if not targets:
            raise RegistryError("a combo needs at least one target")
        parsed: list[ComboTarget] = []
        for t in targets:
            endpoint, model = t.get("endpoint"), t.get("model")
            if not endpoint or not model:
                raise RegistryError("each target needs an `endpoint` and a `model`")
            parsed.append(ComboTarget(endpoint=endpoint, model=model, enabled=t.get("enabled", True)))
        with self._lock:
            data = self._load()
            existing = next((c for c in data["combos"] if c["agent_id"] == agent_id), None)
            record = Combo(
                agent_id=agent_id,
                targets=tuple(parsed),
                enabled=enabled,
                original_provider=(
                    original_provider
                    if original_provider is not None
                    else (existing.get("original_provider") if existing else None)
                ),
                original_model=(
                    original_model
                    if original_model is not None
                    else (existing.get("original_model") if existing else None)
                ),
                created_at=existing["created_at"] if existing else _now(),
                updated_at=_now(),
                # A target/enabled edit doesn't change what most recently answered
                # a real request, so this carries forward exactly like
                # original_provider/original_model above -- only record_dispatch
                # writes these.
                last_target=existing.get("last_target") if existing else None,
                last_attempted=tuple(existing.get("last_attempted", [])) if existing else (),
                last_dispatched_at=existing.get("last_dispatched_at") if existing else None,
            )
            data["combos"] = [
                c for c in data["combos"] if c["agent_id"] != agent_id
            ] + [record.to_json()]
            self._save(data)
            return record

    def set_combo_enabled(self, agent_id: str, enabled: bool) -> Combo:
        combo = self.get_combo(agent_id)
        if combo is None:
            raise RegistryError(f"no such combo: {agent_id!r}")
        return self.upsert_combo(
            agent_id=combo.agent_id,
            targets=[t.to_json() for t in combo.targets],
            enabled=enabled,
        )

    def record_dispatch(self, agent_id: str, *, target: str, attempted: list[str]) -> Combo | None:
        """Called by Router.dispatch after a request actually succeeds, so an
        admin can see which fallback target answered without a log search.

        Deliberately bypasses upsert_combo: this is a metadata bump, not a
        config edit, so it must not re-validate targets against the Catalog,
        must not touch `updated_at` (that reflects an admin edit), and must
        never fail on a Combo an admin just disabled/deleted mid-flight --
        it simply has nothing left to record onto, hence the None return
        instead of RegistryError.
        """
        with self._lock:
            data = self._load()
            for c in data["combos"]:
                if c["agent_id"] == agent_id:
                    c["last_target"] = target
                    c["last_attempted"] = list(attempted)
                    c["last_dispatched_at"] = _now()
                    self._save(data)
                    return Combo.from_json(c)
            return None

    def delete_combo(self, agent_id: str) -> None:
        with self._lock:
            data = self._load()
            remaining = [c for c in data["combos"] if c["agent_id"] != agent_id]
            if len(remaining) == len(data["combos"]):
                raise RegistryError(f"no such combo: {agent_id!r}")
            data["combos"] = remaining
            self._save(data)

    # -- usage (locally observed, never provider-reported) ---------------

    def list_usage(self) -> list[UsageStats]:
        return [UsageStats.from_json(u) for u in self._load()["usage"]]

    def get_usage(self, endpoint: str, model: str) -> UsageStats | None:
        for u in self.list_usage():
            if u.endpoint == endpoint and u.model == model:
                return u
        return None

    def _record_usage(
        self,
        endpoint: str,
        model: str,
        *,
        success: bool,
        failure_class: str | None,
        tokens: dict | None,
    ) -> UsageStats:
        with self._lock:
            data = self._load()
            existing = next(
                (u for u in data["usage"] if u["endpoint"] == endpoint and u["model"] == model),
                None,
            )
            prior = UsageStats.from_json(existing) if existing else UsageStats(endpoint, model)
            now = _now()
            updated = UsageStats(
                endpoint=endpoint,
                model=model,
                request_count=prior.request_count + 1,
                success_count=prior.success_count + (1 if success else 0),
                failure_count=prior.failure_count + (0 if success else 1),
                last_failure_class=failure_class if not success else prior.last_failure_class,
                last_success_at=now if success else prior.last_success_at,
                last_failure_at=prior.last_failure_at if success else now,
                last_used_at=now,
                prompt_tokens=prior.prompt_tokens + (tokens["prompt_tokens"] if tokens else 0),
                completion_tokens=prior.completion_tokens
                + (tokens["completion_tokens"] if tokens else 0),
                total_tokens=prior.total_tokens + (tokens["total_tokens"] if tokens else 0),
            )
            data["usage"] = [
                u for u in data["usage"] if not (u["endpoint"] == endpoint and u["model"] == model)
            ] + [updated.to_json()]
            self._save(data)
            return updated

    def record_usage_success(
        self, endpoint: str, model: str, *, tokens: dict | None = None
    ) -> UsageStats:
        """Called for any non-retryable 2xx response this target produced --
        success in the observability sense, independent of whether this
        target was the Combo's overall winner (see record_dispatch for
        that). `tokens`, when given, must already be the validated
        {prompt_tokens, completion_tokens, total_tokens} shape (see
        llm_router.proxy._extract_usage_tokens) -- this method only adds
        what it is handed, never invents a number."""
        return self._record_usage(endpoint, model, success=True, failure_class=None, tokens=tokens)

    def record_usage_failure(self, endpoint: str, model: str, *, failure_class: str) -> UsageStats:
        """Called for every non-2xx outcome this target produced, whether or
        not it triggered a fallback (a 400/401/404 does not, but is still a
        failure for this target's own health)."""
        return self._record_usage(
            endpoint, model, success=False, failure_class=failure_class, tokens=None
        )
