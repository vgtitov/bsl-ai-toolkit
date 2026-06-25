# Центральный MCP чтения кода 1С как HTTP-сервис (для команды/нескольких разработчиков).
# Исходники конфигураций монтируются томом в /src (read-only); сервис отдаёт MCP по HTTP (SSE).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ripgrep \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mcp>=1.2"

WORKDIR /app
COPY mcp/erp_mcp.py /app/erp_mcp.py

ENV ONEC_SRC_DIR=/src \
    MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000
CMD ["python", "/app/erp_mcp.py"]
