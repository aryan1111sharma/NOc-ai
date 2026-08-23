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

    # --- contributed uploads (see 2026-08-23-kb-document-upload-design.md) ---
    s3_bucket: str = ""                       # empty disables the write tools entirely
    aws_region: str = "ap-south-1"
    contributed_scope: str = "contributed"
    contributed_dir: str = "/knowledge/contributed"  # writable volume nested in the ro mount
    kb_contributor_roles: str = "kb-contributor,ADMIN"
    kb_max_upload_mb: int = 25

    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8106

    @property
    def contributor_roles(self) -> tuple[str, ...]:
        """Comma-separated env value -> tuple. Blank entries are dropped so a
        stray comma cannot create a role that matches a caller with none."""
        return tuple(r.strip() for r in self.kb_contributor_roles.split(",") if r.strip())


settings = Settings()
