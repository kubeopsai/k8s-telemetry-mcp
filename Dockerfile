FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY k8s_telemetry_mcp/ ./k8s_telemetry_mcp/
RUN pip install --no-cache-dir hatchling && pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.11-slim

LABEL org.opencontainers.image.title="K8s Telemetry MCP Server"
LABEL org.opencontainers.image.description="Give your AI assistant read-only access to your Kubernetes cluster"
LABEL org.opencontainers.image.source="https://github.com/kubeopsai/k8s-telemetry-mcp"
LABEL org.opencontainers.image.licenses="Apache-2.0"

RUN useradd --create-home --shell /bin/bash --uid 1001 mcp

COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER mcp

ENV MCP_LOG_LEVEL=INFO \
    MCP_ENABLE_SANITIZATION=true \
    MCP_MAX_LOG_LINES=500 \
    MCP_MAX_QUERY_RANGE_HOURS=24

ENTRYPOINT ["k8s-telemetry-mcp"]
