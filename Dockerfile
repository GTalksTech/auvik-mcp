# Auvik MCP server — HTTP mode. Build context is THIS REPO ROOT:
#
#     docker build -t auvik-mcp:local .
#     docker run -p 8080:8080 -e AUVIK_USERNAME=... -e AUVIK_API_KEY=... \
#                -e AUVIK_REGION=us1 -e MCP_INBOUND_API_KEY=... auvik-mcp:local
#
# Runtime environment:
#   AUVIK_USERNAME        Auvik login email of the API user (plain config)
#   AUVIK_API_KEY         Auvik API key — needs READ, plus Alerts: Edit if you want
#                         auvik_dismiss_alert to work. Supply it as a secret.
#   AUVIK_REGION          the subdomain of your Auvik URL (default below is us1)
#   MCP_INBOUND_API_KEY   inbound key checked by the ASGI middleware; UNSET = every
#                         request except /health is rejected (fail-closed)
#   PORT                  listen port (default 8080)
#
# Scale MUST be one replica (minReplicas=1, maxReplicas=1 on Azure Container
# Apps, replicas=1 on anything else): the dismiss guardrails are process state.
# See docs/HOSTING.md and docs/API-DEVIATIONS.md D-7.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY server.py envelope.py auvik_client.py credentials.py ./
COPY tools/ ./tools/

ENV PORT=8080
ENV AUVIK_REGION=us1
EXPOSE 8080

# stdout is the log stream: one JSON line per tool call, plus the dismissal_audit
# line for every successful write. audit/dismissals.jsonl is container-local and
# best-effort — the stdout line is the durable copy, so ship stdout somewhere
# that is kept.
CMD ["python", "server.py"]
