from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # NOTE: env_file is resolved relative to the PROCESS WORKING DIRECTORY, not
    # this module. A stray .env dropped into services/mcp/kb/ for unrelated
    # local config will silently override these settings under test. The
    # container is unaffected — compose sets every value explicitly and there
    # is no .env at /app.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://qdrant:6333"
    collection: str = "ev_kb"
    embed_model: str = "BAAI/bge-base-en-v1.5"  # 768-dim; bge-small-en-v1.5 = 384, faster
    embed_dim: int = 768                         # MUST match embed_model
    chunk_max_chars: int = 1800                  # ~450 tokens
    chunk_overlap_chars: int = 200
    # With hybrid dense+BM25 re-ranking (store.py), 4 matched top_k=5's
    # recall/MRR on the evaluated KB corpus at ~21% fewer tokens sent to the
    # LLM. Re-check against a given corpus's own eval set before retuning.
    default_top_k: int = 4
    # Relevance floor for kb_search. Below this a hit is noise, and returning noise
    # defeats the "not in the knowledge base" contract in the tool docstring.
    # Calibrated on the Phase-1 corpus: on-topic hits scored 0.58-0.83, off-topic 0.41-0.42.
    min_score_default: float = 0.5
    knowledge_dir: str = "/knowledge"
    scopes_file: str = "/knowledge/scopes.yaml"
    # Departmental KBs live here, not in scopes.yaml, so creating one is a data
    # change on the /knowledge mount rather than a YAML edit plus a rebuild.
    kb_registry_file: str = "/knowledge/kb-registry.json"
    # Read-only view of the control plane's departments.json. The set of valid
    # departments has exactly one definition, and it is not in this codebase.
    departments_file: str = "/config/departments.json"

    # "dept" serves the per-department search/write tools; "admin" serves only
    # the kb_admin_* control tools. Splitting them across two endpoints means a
    # department agent cannot reach KB administration even by mistake, without
    # relying solely on LibreChat-side ACL to hide it.
    kb_role: str = "dept"
    # How often to re-read the registry. The admin plane runs in a separate
    # process, so the dept plane learns about a new KB by noticing the file
    # changed. 0 disables polling (tests, stdio use).
    reload_interval_seconds: float = 5.0


    # --- contributed uploads (see 2026-08-23-kb-document-upload-design.md) ---
    s3_bucket: str = ""                       # empty disables the write tools entirely
    aws_region: str = "ap-south-1"
    contributed_scope: str = "contributed"
    contributed_dir: str = "/knowledge/contributed"  # writable volume nested in the ro mount
    # Transient downloads land here, and this MUST NOT sit inside the tree the
    # corpus walker scans. It previously defaulted to contributed_dir, which
    # meant every uploaded file was written to /knowledge/contributed/<user
    # id>/<name> and then indexed a SECOND time by the next corpus walk — as
    # `contributed/<user id>/<name>`, with the uploader's id embedded in the
    # source string the chat agent cites. A rolled-back upload came back as a
    # real document the same way.
    upload_cache_dir: str = "/var/cache/kb-uploads"
    kb_contributor_roles: str = "kb-contributor,ADMIN"
    # A zero or negative cap would reject every upload with a message that
    # reads like the file is too large, hiding the real fault. Fail at startup
    # instead, where the cause is obvious.
    kb_max_upload_mb: int = Field(default=25, gt=0)

    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8106

    @property
    def contributor_roles(self) -> tuple[str, ...]:
        """Comma-separated env value -> tuple. Blank entries are dropped so a
        stray comma cannot create a role that matches a caller with none."""
        return tuple(r.strip() for r in self.kb_contributor_roles.split(",") if r.strip())


settings = Settings()
