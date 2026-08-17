FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docgen_mcp ./docgen_mcp

# HTTP transport for remote MCP clients (e.g. LibreChat on the same compose network).
ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8103

EXPOSE 8103

CMD ["python", "-m", "docgen_mcp"]
