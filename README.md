# K8s Telemetry MCP Server

**Give your AI assistant read-only access to your Kubernetes cluster's observability stack.**

Connect Amazon Q, Claude, Kiro, Cursor, or any MCP-compatible AI assistant to Loki, Prometheus, Tempo, and the Kubernetes API — via a single Helm install.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.2.0-green.svg)](CHANGELOG.md)
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

> **Label your observability namespace.** The bundled NetworkPolicy only allows egress to
> a namespace labelled `name: <namespace>`. Kubernetes does not add that label for you,
> and without it every query is blocked:
>
> ```bash
> kubectl label namespace monitoring name=monitoring
> ```

### 2. Configure Your AI Assistant

> **Prerequisites:** Your AI assistant runs `kubectl exec` locally. Ensure `kubectl` is in your `PATH` and your active kubeconfig context points to the cluster where the MCP server is installed.

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

## 23 tools Across 6 Categories

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
| `get_configuration_history` | What actually changed on a resource, with field-level before/after diffs. |
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
| `MCP_ALERTMANAGER_URL` | `""` | Alertmanager endpoint (required for `get_alertmanager_history`) |
| `MCP_AWS_REGION` | `us-east-1` | AWS region for CloudTrail/Config/ECR/RDS tools |
| `MCP_DATADOG_API_KEY` | `""` | Datadog API key (overrides Loki/Prometheus) |
| `MCP_CLOUDWATCH_LOG_GROUP` | `""` | CloudWatch log group (overrides Loki) |
| `MCP_ENABLE_SANITIZATION` | `true` | Auto-redact PII and secrets |
| `MCP_MAX_LOG_LINES` | `500` | Max log lines per query |
| `MCP_MAX_QUERY_RANGE_HOURS` | `24` | Max query time range |
| `MCP_LOG_LEVEL` | `INFO` | Log level |

---

## Security

- **Read-only**: All 23 tools are strictly read-only. No write, delete, or mutation operations anywhere in the codebase.
- **No ingress**: Communicates over stdio via `kubectl exec`. No exposed ports, no load balancers, no API key to rotate. Note the corollary: `exec` access to the pod is equivalent to read access to your telemetry, so restrict it accordingly.
- **RBAC**: A scoped `ClusterRole` grants `get`/`list` on events, nodes, deployments, replicasets, and HPAs. No `watch`, no wildcards, no `secrets`, no `pods/exec`, no `pods/log`.
- **NetworkPolicy**: Restricts egress to your observability stack, DNS, and — only when the AWS tools are enabled — outbound HTTPS. Set `networkPolicy.allowAwsApiEgress=false` to remove that last rule.
- **Redaction**: Best-effort regex scrubbing of AWS access keys, JWTs, bearer tokens, `password=`-style assignments, database URIs, emails, and card/SSN patterns before output reaches your assistant. It catches the textbook cases; it does **not** catch modern token formats (`ghp_`, `xoxb-`, `sk_live_`), unlabelled secrets, or Prometheus label values and RDS Performance Insights SQL text. Treat it as defence in depth rather than a compliance boundary — see the [Security wiki page](https://github.com/kubeopsai/k8s-telemetry-mcp/wiki/Security) for the full list of what is and is not covered.
- **Container**: non-root (uid 1001), read-only root filesystem, all capabilities dropped.

### What runs in the pod

The MCP protocol here is stdio-based, and your assistant starts its own server process
through `kubectl exec`. The pod's own process is a small host
(`k8s-telemetry-mcp-host`) that keeps the pod alive for those sessions and answers the
health probes. If you were expecting a long-running server listening on a port, there
isn't one — that is by design.

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

MCP_LOKI_URL=http://localhost:3100 \
MCP_PROMETHEUS_URL=http://localhost:9090 \
python -m k8s_telemetry_mcp.server
```

The server reads MCP requests on stdin, so run it from a terminal and paste a request, or
point a local assistant at it. A quick handshake check:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m k8s_telemetry_mcp.server
```

Before opening a PR:

```bash
ruff check k8s_telemetry_mcp/ tests/
pytest
```

---

## ⚡ From Free Tool to 3 AM Hero

### Tuesday 2:00 PM — The Free Win

You're tracking down a staging issue. You type into Claude Desktop:

> *"Why did the checkout pod restart 10 minutes ago?"*

Claude calls `get_k8s_events` and `query_pod_logs`. In seconds:

> *"The pod was OOMKilled. It hit its 512Mi limit after processing a large JSON payload."*

Fixed in 3 minutes. You would have spent 20 minutes tabbing between Lens, Grafana, and CloudWatch.

---

### Saturday 3:15 AM — The Pain

PagerDuty fires. **High Latency — Payment Gateway.**

You roll out of bed. VPN. Okta. AWS Console. Grafana. Loki. Prometheus. 25 minutes of manual digging to find that a bad database migration locked a table. You roll back the deployment. It's 4:30 AM. Sleep is gone.

The tool that diagnosed your OOMKilled pod in 3 minutes on Tuesday had all the access it needed to find this too — it just needed someone at a keyboard to ask.

---

### Monday 9:00 AM — The Realization

In the incident review, you remember Tuesday. You open the GitHub README and scroll to the bottom.

**What if the tool ran itself when the alert fired?**

That's exactly what [KubeOpsAI Agent](https://kubeopsai.net) does.

---

## 🤖 KubeOpsAI Agent — Automated Incident Response

KubeOpsAI hooks into your Alertmanager webhooks. When an alert fires at 3 AM, it investigates using 18 of these collectors and posts a root-cause analysis to your Slack incident channel — **before PagerDuty even wakes your engineer up.**

> **Read-only by design.** The agent investigates and explains. Your engineer makes the call and runs the fix. This is exactly why security teams approve it in 5 minutes instead of 6 months.

```
Alerting fires at 3:14 AM
        │
        ▼
  KubeOpsAI Agent
  ├── get_k8s_events      → OOMKilled × 3 in 10 min
  ├── query_pod_logs      → cache.put() called 12,000×/min
  ├── get_recent_deployments → checkout-api v2.4.1 at 03:00 UTC
  └── get_pod_metrics     → memory 180Mi → 512Mi limit
        │
        ▼
  Slack #incidents at 3:15 AM
  "Root cause: unbounded cache in v2.4.1.
   Recommended action: rollback to v2.4.0."
        │
        ▼
  Engineer wakes up to answer, not questions.
  Back to sleep by 3:20 AM.
```

- Zero human interaction required
- Runs inside your cluster — your data never leaves your VPC
- Bring your own LLM key (AWS Bedrock, Anthropic, OpenAI)
- A separate commercial product, sold on AWS Marketplace. This MCP server is free and
  open source, and stays that way — KubeOpsAI builds on it.

👉 **[kubeopsai.net](https://kubeopsai.net)**

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/kubeopsai/k8s-telemetry-mcp/issues)
- **Documentation**: [GitHub Wiki](https://github.com/kubeopsai/k8s-telemetry-mcp/wiki)
- **Email**: hello@kubeopsai.net
