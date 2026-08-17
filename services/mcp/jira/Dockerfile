FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jira_mcp ./jira_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8102

EXPOSE 8102

# JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN must be provided at runtime (env / compose).
CMD ["python", "-m", "jira_mcp"]
