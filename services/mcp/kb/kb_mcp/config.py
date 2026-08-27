from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://qdrant:6333"
    collection: str = "ev_kb"
    embed_model: str = "BAAI/bge-base-en-v1.5"  # 768-dim; bge-small-en-v1.5 = 384, faster
    embed_dim: int = 768                         # MUST match embed_model
    chunk_max_chars: int = 1800                  # ~450 tokens
    chunk_overlap_chars: int = 200
    default_top_k: int = 5
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


    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8106


settings = Settings()
