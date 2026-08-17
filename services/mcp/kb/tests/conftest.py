# kb_mcp.config has no required (default-less) fields, but keep tests hermetic.
import os
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
