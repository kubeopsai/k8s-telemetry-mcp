# K8s Telemetry MCP Server

**Enterprise Kubernetes Observability for AI Assistants** — Give your AI assistant (Amazon Q, Claude, Amazon Bedrock) read-only access to your cluster's observability stack: Loki, Prometheus, and Tempo.

[![AWS Marketplace](https://img.shields.io/badge/AWS%20Marketplace-Available-orange)](https://aws.amazon.com/marketplace)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-kubeopsai-black)](https://github.com/kubeopsai/k8s-telemetry-mcp)

---

## How It Works

This server implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io), which is a standard that allows AI assistants to call external tools. When you ask your AI assistant a question like *"Why did the payment pod crash?"*, it calls the appropriate MCP tool, receives structured observability data, and synthesizes a natural-language answer.

**The AI intelligence comes from your existing assistant** — Amazon Q, Claude, or any Bedrock-powered agent. This server provides the data layer: structured, sanitized access to your cluster's logs, metrics, and traces.

```
You ──► AI Assistant (Amazon Q / Claude / Bedrock)
              │
              ▼  MCP tool calls
        K8s Telemetry MCP Server
              │
        ┌─────┼─────┐
        ▼     ▼     ▼
      Loki  Prometheus  Tempo
```

---

## Getting Started

Follow these steps after purchasing from AWS Marketplace.

### Prerequisites

Before installing, confirm you have:

- An active EKS cluster (Kubernetes 1.24+)
- `kubectl` configured and pointing at that cluster
- `helm` 3.x installed locally
- An existing observability stack in the cluster:
  - [Grafana Loki](https://grafana.com/oss/loki/) for logs **with a log shipper configured** (e.g. Promtail, Fluent Bit, or Fluentd) to collect pod logs and push them to Loki. Without a shipper, `query_pod_logs` and `analyze_logs` will return empty results.
  - [Prometheus](https://prometheus.io/) for metrics
  - [Grafana Tempo](https://grafana.com/oss/tempo/) for traces (optional)
- Your **AWS Marketplace product code** — visible on the [AWS Marketplace Management Portal](https://aws.amazon.com/marketplace/management/) under your subscription

---

### Step 1 — Authenticate with AWS ECR

AWS Marketplace container products are distributed via Amazon ECR. You must authenticate Docker with ECR before Kubernetes can pull the image.

```bash
aws ecr get-login-password --region <REGION> | \
  docker login --username AWS --password-stdin \
  <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
```

Then create an image pull secret in your cluster so the pod can pull the image:

```bash
kubectl create namespace monitoring

kubectl create secret docker-registry aws-marketplace-secret \
  --namespace monitoring \
  --docker-server=<AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com \
  --docker-username=AWS \
  --docker-password=$(aws ecr get-login-password --region <REGION>)
```

---

### Step 2 — Install via Helm

Add the Helm repository and install the chart, substituting your real values:

```bash
helm repo add k8s-telemetry-mcp https://kubeopsai.github.io/k8s-telemetry-mcp
helm repo update

helm install k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring \
  --set image.repository=<AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp \
  --set image.tag=1.0.0 \
  --set imagePullSecrets[0].name=aws-marketplace-secret \
  --set config.marketplaceProductCode=<YOUR_PRODUCT_CODE> \
  --set config.lokiUrl=http://loki.monitoring:3100 \
  --set config.prometheusUrl=http://prometheus.monitoring:9090 \
  --set config.tempoUrl=http://tempo.monitoring:3200
```

For repeatable deployments, use a `values.yaml` file instead:

```yaml
image:
  repository: <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp
  tag: "1.0.0"

imagePullSecrets:
  - name: aws-marketplace-secret

config:
  marketplaceProductCode: "<YOUR_PRODUCT_CODE>"
  lokiUrl: "http://loki.monitoring:3100"
  prometheusUrl: "http://prometheus.monitoring:9090"
  tempoUrl: "http://tempo.monitoring:3200"
```

```bash
helm install k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring \
  -f values.yaml
```

---

### Step 3 — Verify the Deployment

Check the pod is running and the license check passed:

```bash
# Pod should be in Running state
kubectl get pods -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp

# Logs should show "AWS Marketplace license verified successfully"
kubectl logs -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp
```

If the pod exits immediately, the license check failed. Common causes:
- `marketplaceProductCode` is empty or incorrect
- The EKS node's IAM role does not have `aws-marketplace:RegisterUsage` permission
- The pod cannot reach the AWS License Manager endpoint (check your NetworkPolicy or VPC routing)

To grant the required IAM permissions, attach this policy to your EKS node role:

```json
{
  "Effect": "Allow",
  "Action": [
    "aws-marketplace:RegisterUsage",
    "marketplace-entitlement:GetEntitlements"
  ],
  "Resource": "*"
}
```

Both permissions are required. `RegisterUsage` records metering for billing. `GetEntitlements` verifies your active subscription tier at startup — if it is missing, the server falls back to the configured `MCP_MARKETPLACE_TIER` value with a warning.

---

### Step 4 — Configure Your AI Assistant

The MCP server communicates over **stdio** — your AI assistant runs `kubectl exec` as a subprocess and pipes messages through it. There is no HTTP port to expose or API key to manage.

Add the following snippet to your AI assistant's MCP configuration file, replacing `monitoring` with your namespace if different.

**Amazon Q Developer** — edit `~/.aws/amazonq/mcp.json`:

```json
{
  "mcpServers": {
    "k8s-telemetry": {
      "command": "kubectl",
      "args": [
        "exec", "-i",
        "-n", "monitoring",
        "deploy/k8s-telemetry-mcp",
        "--",
        "k8s-telemetry-mcp"
      ]
    }
  }
}
```

**Claude Desktop** — edit `claude_desktop_config.json` (location varies by OS):

```json
{
  "mcpServers": {
    "k8s-telemetry": {
      "command": "kubectl",
      "args": [
        "exec", "-i",
        "-n", "monitoring",
        "deploy/k8s-telemetry-mcp",
        "--",
        "k8s-telemetry-mcp"
      ]
    }
  }
}
```

Restart your AI assistant after saving the config. It will discover all 12 tools automatically on next launch.

> **Note:** The user running the AI assistant must have `kubectl exec` permission on the `k8s-telemetry-mcp` deployment. This is controlled by your cluster's existing RBAC — no additional permissions are granted by this product.

---

### Step 5 — Start Asking Questions

Your AI assistant can now answer questions like:

- *"Why did the payment pod crash in the last hour?"*
- *"Show me CPU and memory usage for the api-gateway service"*
- *"Is the checkout service meeting its SLO this week?"*
- *"What happened to the order service between 2pm and 3pm?"*
- *"Which namespace is consuming the most resources?"*

---

## Tools Reference

The server exposes 12 tools across four categories. Your AI assistant selects and calls these automatically based on your question. You can also instruct it to use specific tools directly.

---

### Log Tools (Loki)

#### `query_pod_logs`

Retrieves raw logs from a specific pod or set of pods. All output is automatically sanitized to redact PII and secrets before being returned.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pod_name` | string | required | Pod name or regex pattern (e.g., `payment-service` or `payment-.*`) |
| `namespace` | string | `default` | Kubernetes namespace |
| `container` | string | none | Filter to a specific container within the pod |
| `timeframe_minutes` | integer | `60` | How many minutes of logs to retrieve. Clamped to `MCP_MAX_QUERY_RANGE_HOURS`. |
| `limit` | integer | `100` | Maximum number of log lines. Clamped to `MCP_MAX_LOG_LINES` (default 500). |

**Example prompts:**
- *"Show me the last 30 minutes of logs from the payment-service pod in the prod namespace"*
- *"Get logs from any pod matching `worker-.*` in the jobs namespace, last 200 lines"*

---

#### `query_logs_custom`

Executes a raw [LogQL](https://grafana.com/docs/loki/latest/query/) query directly against Loki. Use this when you need filtering, parsing, or aggregation beyond what `query_pod_logs` provides.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | A valid LogQL query (e.g., `{namespace="prod"} \|= "error"`) |
| `timeframe_minutes` | integer | `60` | Time range for the query |
| `limit` | integer | `100` | Maximum number of log lines |

**Example prompts:**
- *"Run this LogQL query: `{namespace="prod", app="api"} |= "timeout" | json | latency > 1000`"*
- *"Find all logs in the prod namespace containing 'NullPointerException' in the last 2 hours"*

---

### Metrics Tools (Prometheus)

#### `get_pod_metrics`

Retrieves a specific metric type for a pod or set of pods using pre-built PromQL queries.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pod_name` | string | required | Pod name or regex pattern |
| `namespace` | string | `default` | Kubernetes namespace |
| `metric_type` | string | `cpu` | One of: `cpu`, `memory`, `restarts`, `network_rx`, `network_tx` |

**Metric types explained:**
- `cpu` — CPU usage in cores (5-minute rate)
- `memory` — Working set memory in bytes
- `restarts` — Total container restart count
- `network_rx` — Network bytes received per second
- `network_tx` — Network bytes transmitted per second

**Example prompts:**
- *"What is the current CPU usage of the api-gateway pod?"*
- *"How many times has the worker pod restarted today?"*
- *"Show me memory usage for all pods matching `cache-.*` in the prod namespace"*

---

#### `query_prometheus`

Executes a raw [PromQL](https://prometheus.io/docs/prometheus/latest/querying/basics/) query. Supports both instant queries (a single point in time) and range queries (a series of values over time).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | A valid PromQL expression |
| `range_query` | boolean | `false` | Set to `true` to get values over a time range instead of a single instant |
| `timeframe_minutes` | integer | `60` | Time range when `range_query` is `true` |
| `step` | string | `1m` | Resolution step for range queries (e.g., `1m`, `5m`, `1h`) |

**Example prompts:**
- *"Run this PromQL query: `rate(http_requests_total{status=~"5.."}[5m])`"*
- *"Show me the p99 request latency for the checkout service over the last hour as a range query with 5-minute steps"*

---

#### `get_cluster_health`

Returns a snapshot of overall cluster health. No parameters required.

Returns the following metrics:
- `node_count` — Total number of nodes
- `pod_count` — Total number of pods
- `running_pods` — Pods in Running phase
- `failed_pods` — Pods in Failed phase
- `pending_pods` — Pods in Pending phase
- `cpu_utilization` — Average CPU utilization across all nodes (0.0–1.0)
- `memory_utilization` — Average memory utilization across all nodes (0.0–1.0)

**Example prompts:**
- *"What is the current health of the cluster?"*
- *"Are there any failed or pending pods right now?"*

---

### Trace Tools (Tempo)

#### `get_trace`

Retrieves a complete distributed trace by its trace ID, including all spans. Sensitive data in span attributes is automatically sanitized.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `trace_id` | string | required | A hexadecimal trace ID, 16–32 characters (e.g., `4bf92f3577b34da6`) |

**Example prompts:**
- *"Show me the full trace for trace ID `4bf92f3577b34da6a3ce929d0e0e4736`"*
- *"Retrieve trace `abc123def456789a` from Tempo"*

---

#### `search_traces`

Searches for traces matching a combination of filters. Useful for finding slow requests, error traces, or traces from a specific service.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | string | none | Filter by service name |
| `operation` | string | none | Filter by operation/span name |
| `tags` | object | none | Filter by span tags as key-value pairs (e.g., `{"http.status_code": "500"}`) |
| `min_duration` | string | none | Minimum trace duration (e.g., `100ms`, `1s`, `2.5s`) |
| `max_duration` | string | none | Maximum trace duration |
| `timeframe_minutes` | integer | `60` | How far back to search |
| `limit` | integer | `20` | Maximum number of traces to return (max 100) |

**Example prompts:**
- *"Find all traces from the checkout service that took longer than 2 seconds in the last hour"*
- *"Search for error traces in the payment service from the last 30 minutes"*
- *"Find traces with the tag `http.status_code=500` in the api-gateway service"*

---

### Analytics Tools

These tools combine data from multiple sources (Loki, Prometheus, Tempo) and apply pattern detection to return structured insights rather than raw data.

#### `analyze_logs`

Fetches logs for a service and runs pattern analysis to detect errors, categorize them by type, identify recurring patterns, and generate actionable recommendations.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | string | required | Service or pod name (supports regex) |
| `namespace` | string | `default` | Kubernetes namespace |
| `timeframe_minutes` | integer | `60` | Time window to analyze |
| `limit` | integer | `500` | Maximum log lines to analyze |

**What it detects:**
- Errors categorized by type: `error`, `timeout`, `connection`, `memory`, `auth`, `not_found`, `rate_limit`, `crash`
- Time distribution of errors (first occurrence, last occurrence, error percentage)
- Recurring log patterns and repetition ratio
- Actionable recommendations based on detected error types

**Example prompts:**
- *"Analyze the logs for the payment-service and tell me what's wrong"*
- *"Are there any memory or connection errors in the api-gateway in the last 2 hours?"*

---

#### `build_incident_timeline`

Builds a unified, chronological timeline of significant events by correlating logs, CPU metrics, and traces for a service. Designed to accelerate root cause analysis during incidents.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | string | required | The affected service name |
| `namespace` | string | `default` | Kubernetes namespace |
| `timeframe_minutes` | integer | `60` | Time window to analyze |

**What it correlates:**
- Log events classified as `error` or `warning`
- Metric anomalies from Prometheus
- Failed or slow traces from Tempo (HTTP status ≥ 400)

All events are sorted chronologically and tagged with their source (log, metric, or trace) and severity.

**Example prompts:**
- *"Build an incident timeline for the checkout service for the last 45 minutes"*
- *"What sequence of events led to the api-gateway degradation this morning?"*

---

#### `enrich_alert`

When an alert fires, this tool gathers full context in a single call: recent error logs, current CPU/memory/restart metrics, recent traces, and suggested follow-up queries. Designed to give on-call engineers immediate situational awareness.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `alert_name` | string | required | The name of the alert that fired (e.g., `HighErrorRate`) |
| `service_name` | string | required | The affected service |
| `namespace` | string | `default` | Kubernetes namespace |
| `timeframe_minutes` | integer | `30` | Context window (capped at 60 minutes) |

**What it returns:**
- Recent error log samples
- Current CPU, memory, and restart metrics
- Trace error rate for the window
- Recommendations based on log analysis
- Suggested LogQL and PromQL queries for deeper investigation

**Example prompts:**
- *"The HighMemoryUsage alert just fired for the worker service in prod — what's happening?"*
- *"Enrich the PodCrashLooping alert for the payment-service in the payments namespace"*

---

#### `get_resource_costs`

Calculates resource cost attribution by namespace based on current CPU and memory usage from Prometheus. Useful for FinOps visibility and identifying over-provisioned workloads.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `namespace` | string | none | Filter to a specific namespace. Omit to see all namespaces. |
| `timeframe_minutes` | integer | `60` | Usage averaging window |

**What it returns:**
- Per-namespace breakdown of CPU cores used, memory (GB), hourly cost, and projected monthly cost
- Total cluster hourly and monthly cost
- Top 5 highest-cost namespaces
- Optimization suggestions (e.g., namespaces with very low utilization)

> **Note:** Cost estimates use default rates of $0.05/core-hour for CPU and $0.01/GB-hour for memory. These are approximations for relative comparison, not exact cloud billing figures.

**Example prompts:**
- *"Which namespace is spending the most on compute resources?"*
- *"Show me the resource cost breakdown for the prod namespace"*
- *"Are there any namespaces with very low utilization that could be consolidated?"*

---

#### `check_slo_status`

Checks Service Level Objective (SLO) compliance for a service by querying Prometheus for request counts, error counts, and latency. Returns error budget remaining, burn rate, and compliance status.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | string | required | The service to check |
| `namespace` | string | `default` | Kubernetes namespace |
| `availability_target` | float | `0.999` | Target availability as a decimal (e.g., `0.999` = 99.9%) |
| `latency_target_ms` | float | `500.0` | Target latency in milliseconds at the given percentile |
| `latency_percentile` | float | `0.99` | Latency percentile to measure (e.g., `0.99` = p99) |
| `window_hours` | integer | `24` | SLO measurement window in hours (1–720) |

**What it returns:**
- Availability SLO: target vs. current, met/not met, gap
- Latency SLO: target vs. current p99 (or configured percentile), met/not met
- Error budget: total allowed errors, errors consumed, percentage remaining
- Burn rate: how fast the error budget is being consumed relative to a sustainable pace
- Time to exhaustion (if burn rate > 1)
- Overall status: `healthy`, `warning`, `degraded`, or `critical`
- Recommendations based on current status

**Burn rate explained:** A burn rate of `1.0` means you are consuming your error budget at exactly the sustainable rate. A burn rate of `2.0` means you will exhaust your monthly error budget in half the expected time.

**Example prompts:**
- *"Is the checkout service meeting its 99.9% availability SLO this week?"*
- *"Check the SLO status for the api-gateway with a 200ms p95 latency target over the last 7 days"*
- *"How much error budget does the payment service have left this month?"*

---

## Security

### Automatic PII and Secret Redaction

All data returned by every tool is automatically sanitized before it reaches your AI assistant. The following patterns are detected and replaced with `[REDACTED_<TYPE>]`:

| Type | Example |
|------|---------|
| Credit card numbers | Visa, Mastercard, Amex, Discover |
| Social Security Numbers | `123-45-6789` |
| AWS access keys | `AKIA...` |
| AWS secret keys | `aws_secret_access_key=...` |
| Generic API keys and tokens | `api_key=...`, `access_token=...` |
| Passwords in logs | `password=...`, `pwd=...` |
| JWT tokens | `eyJ...` |
| Bearer tokens | `Bearer abc123...` |
| Private IP addresses | RFC 1918 ranges (10.x, 172.16–31.x, 192.168.x) |
| Email addresses | `user@example.com` |
| Phone numbers | US format |
| Kubernetes secret data | Base64-encoded `data:` blocks |
| Database connection strings | `postgres://`, `mongodb://`, `redis://`, etc. |

Sanitization is enabled by default and controlled by the `MCP_ENABLE_SANITIZATION` environment variable.

### Network Policy

The Helm chart deploys a Kubernetes `NetworkPolicy` that restricts all egress from the server pod to only:
- Loki on port 3100
- Prometheus on port 9090
- Tempo on port 3200
- DNS on port 53

No other outbound connections are permitted.

### RBAC

The server's Kubernetes service account has **no Kubernetes API permissions**. It communicates exclusively with the observability stack over HTTP. It cannot read secrets, list pods, or perform any cluster operations.

### Read-Only

All 12 tools are read-only. There are no write, delete, or mutation operations of any kind.

---

## Configuration

All settings are configured via environment variables with the `MCP_` prefix.

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MCP_LOKI_URL` | `http://loki:3100` | Loki endpoint |
| `MCP_LOKI_TIMEOUT` | `30` | Loki request timeout in seconds |
| `MCP_PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus endpoint |
| `MCP_PROMETHEUS_TIMEOUT` | `30` | Prometheus request timeout in seconds |
| `MCP_TEMPO_URL` | `http://tempo:3200` | Tempo endpoint |
| `MCP_TEMPO_TIMEOUT` | `30` | Tempo request timeout in seconds |
| `MCP_ENABLE_SANITIZATION` | `true` | Enable automatic PII and secret redaction |
| `MCP_MAX_LOG_LINES` | `500` | Maximum log lines returned per query |
| `MCP_MAX_QUERY_RANGE_HOURS` | `24` | Maximum time range allowed for any query |
| `MCP_LOG_LEVEL` | `INFO` | Server log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MCP_AWS_MARKETPLACE_PRODUCT_CODE` | `` | Your AWS Marketplace product code (required in production) |
| `MCP_MARKETPLACE_TIER` | `professional` | Subscription tier: `standard`, `professional`, or `enterprise` |
| `MCP_LOCAL_DEV` | `false` | Set `true` to bypass license check for local development only |

---

## Helm Configuration

Full reference for `values.yaml`:

```yaml
image:
  repository: <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp
  tag: "1.0.0"

imagePullSecrets:
  - name: aws-marketplace-secret

config:
  # AWS Marketplace (required in production)
  marketplaceProductCode: "<YOUR_PRODUCT_CODE>"
  marketplaceTier: "professional"  # standard | professional | enterprise

  # Observability endpoints
  lokiUrl: "http://loki:3100"
  prometheusUrl: "http://prometheus:9090"
  tempoUrl: "http://tempo:3200"

  # Limits
  enableSanitization: true
  maxLogLines: 500
  maxQueryRangeHours: 24
  logLevel: "INFO"

networkPolicy:
  enabled: true

resources:
  limits:
    cpu: 500m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi
```

---

## AWS Marketplace

This product is available on AWS Marketplace as a container product.

- **Use Committed Spend** — Pay with your existing AWS budget
- **Consolidated Billing** — Appears on your monthly AWS invoice
- **Enterprise Support** — SLA-backed support available

### Pricing

| Tier | Price | Features |
|------|-------|----------|
| Standard | $29/month | 1 namespace, 100 log lines, 8 core tools |
| Professional | $99/month | Unlimited namespaces, 500 log lines, all 12 tools |
| Enterprise | $299/month | Unlimited namespaces, 5000 log lines, 72h query range, all 12 tools |

---

## Development

### Prerequisites

- Python 3.11+
- Docker
- Helm 3.x

### Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run the server locally (license check disabled via local dev flag)
MCP_LOCAL_DEV=true python -m src.server
```

### Run Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=src
```

### Build Docker Image

```bash
docker build -t k8s-telemetry-mcp:latest .
```

---

## Support

- **Documentation**: https://github.com/kubeopsai/k8s-telemetry-mcp/wiki
- **Issues**: https://github.com/kubeopsai/k8s-telemetry-mcp/issues
- **Email**: kubeopsai@gmail.com

## License

MIT License — See [LICENSE](LICENSE) for details.
