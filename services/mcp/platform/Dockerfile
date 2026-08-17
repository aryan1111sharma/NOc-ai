FROM python:3.12-slim

WORKDIR /app

# git CLI is required by platform_git (shells out to `git`, read-only inspection only).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY platform_mcp ./platform_mcp

# HTTP transport for remote MCP clients (e.g. LibreChat on the same compose network).
ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8107 \
    REPO_ROOT=/workspace

EXPOSE 8107

CMD ["python", "-m", "platform_mcp"]
