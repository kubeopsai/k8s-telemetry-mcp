# K8s Telemetry MCP Server

**Give your AI assistant read-only access to your Kubernetes cluster's observability stack.**

Connect Amazon Q, Claude, Kiro, Cursor, or any MCP-compatible AI assistant to Loki, Prometheus, Tempo, and the Kubernetes API — via a single Helm install.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.1-green.svg)](CHANGELOG.md)
[![Helm](https://img.shields.io/badge/Helm-3.x-blue.svg)](helm/k8s-telemetry-mcp)

---

## How It Works

```
You ──► AI Assistant (Amazon Q / Claude / Kiro / Cursor)
              │
              ▼  MCP tool calls (stdio via kubectl exec)
        K8s Telemetry MCP Server
              │
        ┌─────┼──────┬──────────┬──────┐
        ▼     ▼      ▼          ▼      ▼
      Loki  Prom   Tempo    K8s API   AWS
```

The server runs as a pod inside your cluster. Your AI assistant connects via `kubectl exec` — no ingress, no exposed ports, no API keys.

---

## Quick Start

### 1. Install via Helm

```bash
helm repo add k8s-telemetry-mcp https://kubeopsai.github.io/k8s-telemetry-mcp
helm repo update

helm install k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring --create-namespace \
  --set config.lokiUrl=http://loki.monitoring:3100 \
  --set config.prometheusUrl=http://prometheus-server.monitoring:9090 \
  --set config.tempoUrl=http://tempo.monitoring:3200
```

### 2. Configure Your AI Assistant

**Amazon Q Developer** — `~/.aws/amazonq/mcp.json`:

```json
{
  "mcpServers": {
    "k8s-telemetry": {
      "command": "kubectl",
      "args": ["exec", "-i", "-n", "monitoring", "deploy/k8s-telemetry-mcp", "--", "k8s-telemetry-mcp"]
    }
  }
}
```

**Claude Desktop / Kiro / Cursor** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "k8s-telemetry": {
      "command": "kubectl",
      "args": ["exec", "-i", "-n", "monitoring", "deploy/k8s-telemetry-mcp", "--", "k8s-telemetry-mcp"]
    }
  }
}
```

### 3. Start Asking Questions

```
"Why did the payment pod crash in the last hour?"
"Is the checkout service meeting its 99.9% SLO this week?"
"Which namespace is consuming the most resources?"
"Who deleted the payment-service deployment?"
"Which ECR images have critical vulnerabilities?"
"Why is my pod pending? Show me the Kubernetes events."
"What deployments went out in the last 30 minutes?"
"Is my RDS instance showing slow queries?"
```

---

## 22 Tools Across 6 Categories

### Logs
| Tool | Description |
|------|-------------|
| `query_pod_logs` | Query logs from a pod or regex pattern. PII/secrets auto-redacted. |
| `query_logs_custom` | Execute a raw LogQL / Datadog / CloudWatch Insights query. |

### Metrics
| Tool | Description |
|------|-------------|
| `get_pod_metrics` | CPU, memory, restarts, network for a pod. |
| `query_prometheus` | Execute a raw PromQL query (instant or range). |
| `get_cluster_health` | Node count, pod status, CPU/memory utilization snapshot. |

### Traces
| Tool | Description |
|------|-------------|
| `get_trace` | Retrieve a full distributed trace by ID from Tempo. |
| `search_traces` | Search traces by service, operation, tags, or duration. |

### Analytics
| Tool | Description |
|------|-------------|
| `analyze_logs` | Pattern detection, error categorization, and recommendations. |
| `build_incident_timeline` | Correlate logs, metrics, and traces into a chronological timeline. |
| `enrich_alert` | Full context for a firing alert: logs + metrics + traces + suggestions. |
| `get_resource_costs` | Cost attribution by namespace with optimization suggestions. |
| `check_slo_status` | Error budget, burn rate, and SLO compliance status. |

### Kubernetes
| Tool | Description |
|------|-------------|
| `get_k8s_events` | Events for a namespace or pod — answers OOMKilled/Pending/CrashLoop questions. |
| `get_scaling_history` | HPA scaling history and current autoscaler status. |
| `get_node_pressure` | Node MemoryPressure, DiskPressure, PIDPressure, and eviction status. |
| `get_recent_deployments` | What was rolled out in the last N minutes. |
| `get_alertmanager_history` | Recent alerts and active silences from Alertmanager. |

### AWS
| Tool | Description |
|------|-------------|
| `query_cloudtrail` | Search CloudTrail events by keyword, event name, user, or resource. |
| `get_resource_history` | Full audit trail for a specific AWS resource ID or ARN. |
| `get_resource_compliance` | AWS Config compliance status — drift and non-compliant rules. |
| `get_image_vulnerabilities` | ECR image vulnerability findings via Inspector v2 or basic scan. |
| `get_database_insights` | RDS Performance Insights and ElastiCache CloudWatch metrics. |

---

## Configuration

All settings use the `MCP_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_LOKI_URL` | `""` | Loki endpoint |
| `MCP_PROMETHEUS_URL` | `""` | Prometheus endpoint |
| `MCP_TEMPO_URL` | `""` | Tempo endpoint |
| `MCP_ALERTMANAGER_URL` | `""` | Alertmanager endpoint |
| `MCP_AWS_REGION` | `us-east-1` | AWS region for CloudTrail/ECR/RDS tools |
| `MCP_DATADOG_API_KEY` | `""` | Datadog API key (overrides Loki/Prometheus) |
| `MCP_CLOUDWATCH_LOG_GROUP` | `""` | CloudWatch log group (overrides Loki) |
| `MCP_ENABLE_SANITIZATION` | `true` | Auto-redact PII and secrets |
| `MCP_MAX_LOG_LINES` | `500` | Max log lines per query |
| `MCP_MAX_QUERY_RANGE_HOURS` | `24` | Max query time range |
| `MCP_LOG_LEVEL` | `INFO` | Log level |

---

## Security

- **Read-only**: All 22 tools are strictly read-only. No write, delete, or mutation operations.
- **Auto-sanitization**: PII, secrets, tokens, AWS keys, JWTs, and database connection strings are redacted before reaching your AI assistant.
- **No ingress**: Communicates over stdio via `kubectl exec`. No exposed ports, no load balancers.
- **NetworkPolicy**: The Helm chart deploys a `NetworkPolicy` restricting egress to only your observability stack.
- **RBAC**: A scoped `ClusterRole` grants read-only access to events, nodes, deployments, HPAs, and replicasets.

---

## Backends Supported

| Backend | Logs | Metrics | Traces |
|---------|------|---------|--------|
| Loki + Prometheus + Tempo | ✓ | ✓ | ✓ |
| Datadog | ✓ | ✓ | — |
| CloudWatch | ✓ | ✓ | — |

---

## Local Development

```bash
pip install -e ".[dev]"
MCP_LOCAL_DEV=true MCP_LOKI_URL=http://localhost:3100 MCP_PROMETHEUS_URL=http://localhost:9090 python -m src.server
```

---

## ⚡ Automated 3 AM Incident Response

This MCP server is great for **manual querying** from your AI assistant during business hours.

But what about when Alertmanager fires at 3 AM and nobody is at a keyboard?

**The Promtops Autonomous Slack Bot** hooks into your Alertmanager webhooks, automatically runs this diagnostic engine across all 22 tools, and posts a complete root-cause analysis to your Slack incident channel — before your on-call engineer opens their laptop.

- Zero human interaction required
- Runs inside your cluster (outbound-only, no new attack surface)
- Bring your own LLM key (AWS Bedrock, Anthropic, OpenAI)
- $199/month on AWS Marketplace

👉 **[Check out the Promtops Autonomous Slack Bot](https://kubeopsai.net)**

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/kubeopsai/k8s-telemetry-mcp/issues)
- **Documentation**: [GitHub Wiki](https://github.com/kubeopsai/k8s-telemetry-mcp/wiki)
- **Email**: hello@kubeopsai.net
