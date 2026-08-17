FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kb_mcp ./kb_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8106 \
    QDRANT_URL=http://qdrant:6333 \
    KNOWLEDGE_DIR=/knowledge

EXPOSE 8106

CMD ["python", "-m", "kb_mcp"]
