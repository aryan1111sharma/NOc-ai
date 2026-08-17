FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY noc_mcp ./noc_mcp

# HTTP transport for remote MCP clients (e.g. LibreChat on the same compose network).
ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8100

EXPOSE 8100

# NOC_BASE_URL / NOC_EMAIL / NOC_PASSWORD must be provided at runtime (env / compose).
CMD ["python", "-m", "noc_mcp"]
