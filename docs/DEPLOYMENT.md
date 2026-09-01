# Deployment Guide

This guide covers deploying K8s Telemetry MCP Server to your EKS cluster.

## Prerequisites

- EKS cluster with kubectl access
- Helm 3.x installed
- Existing observability stack:
  - Loki for logs
  - Prometheus for metrics
  - Tempo for traces (optional)

## Installation

### Option 1: Helm (Recommended)

```bash
# Add the Helm repository
helm repo add k8s-telemetry-mcp https://kubeopsai.github.io/k8s-telemetry-mcp
helm repo update

# Install with default values
helm install k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring \
  --create-namespace

# Or install with custom values
helm install k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring \
  --set config.lokiUrl=http://loki.monitoring:3100 \
  --set config.prometheusUrl=http://prometheus.monitoring:9090 \
  --set config.tempoUrl=http://tempo.monitoring:3200
```

### Option 2: Manual Deployment

```bash
# Clone the repository
git clone https://github.com/kubeopsai/k8s-telemetry-mcp.git
cd k8s-telemetry-mcp

# Install from local chart
helm install k8s-telemetry-mcp ./helm/k8s-telemetry-mcp \
  --namespace monitoring \
  -f my-values.yaml
```

## Configuration

### Basic Configuration

Create a `values.yaml` file:

```yaml
config:
  lokiUrl: "http://loki:3100"
  prometheusUrl: "http://prometheus:9090"
  tempoUrl: "http://tempo:3200"
  enableSanitization: true
  maxLogLines: 500
  maxQueryRangeHours: 24
  logLevel: "INFO"
```

### Licensing

There is none. This MCP server is free and open source, needs no product code, no
entitlement check and no marketplace IAM permissions, and it is not sold anywhere.

Earlier versions of this page documented `MCP_AWS_MARKETPLACE_PRODUCT_CODE` and
`MCP_MARKETPLACE_TIER`. Nothing ever read those variables, and the Helm chart no longer
sets them. If you have them in a values file, delete them.

The paid product built on this server is
[KubeOpsAI](https://github.com/kubeopsai/kubeopsai-agent), which is sold on AWS Marketplace
and carries its own entitlement checks and IAM requirements.

### Resource Limits

```yaml
resources:
  limits:
    cpu: 500m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi
```

### Network Policy

The chart includes a NetworkPolicy by default. Customize egress rules:

```yaml
networkPolicy:
  enabled: true
  egressRules:
    - to:
        - namespaceSelector:
            matchLabels:
              name: monitoring
      ports:
        - protocol: TCP
          port: 3100  # Loki
        - protocol: TCP
          port: 9090  # Prometheus
        - protocol: TCP
          port: 3200  # Tempo
```

## Connecting AI Assistants

### Claude Desktop

Add to `~/.config/claude/config.json`:

```json
{
  "mcpServers": {
    "k8s-telemetry": {
      "command": "kubectl",
      "args": [
        "exec", "-i", "-n", "monitoring",
        "deploy/k8s-telemetry-mcp", "--",
        "k8s-telemetry-mcp"
      ]
    }
  }
}
```

### Amazon Q Developer

Configure in your IDE settings to use the MCP server endpoint.

### Custom Integration

For programmatic access, use kubectl port-forward:

```bash
kubectl port-forward -n monitoring deploy/k8s-telemetry-mcp 8080:8080
```

## Verification

### Check Deployment Status

```bash
kubectl get pods -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp
```

### View Logs

```bash
kubectl logs -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp
```

### Test Connection

```bash
# Verify the pod is running and tools are registered
kubectl exec -it -n monitoring deploy/k8s-telemetry-mcp -- \
  python -c "import importlib; m = importlib.import_module('src.server'); print('OK')"
```

## Troubleshooting

### Pod Not Starting

1. Check image pull secrets:
   ```bash
   kubectl describe pod -n monitoring -l app.kubernetes.io/name=k8s-telemetry-mcp
   ```

2. Verify ECR authentication:
   ```bash
   kubectl get secret aws-marketplace-secret -n monitoring -o yaml
   ```

### Cannot Connect to Loki/Prometheus

1. Verify service URLs:
   ```bash
   kubectl get svc -n monitoring
   ```

2. Check NetworkPolicy:
   ```bash
   kubectl get networkpolicy -n monitoring
   ```

3. Test connectivity from pod:
   ```bash
   kubectl exec -it -n monitoring deploy/k8s-telemetry-mcp -- \
     curl -s http://loki:3100/ready
   ```

### Logs Not Returning

1. **Verify a log shipper is running** — Loki does not collect logs on its own. You need Promtail, Fluent Bit, or Fluentd pushing logs into Loki:
   ```bash
   kubectl get pods -n monitoring | grep -E 'promtail|fluent'
   ```
   If nothing is returned, install Promtail:
   ```bash
   helm upgrade --install promtail grafana/promtail \
     --namespace monitoring \
     --set config.lokiAddress=http://loki:3100/loki/api/v1/push
   ```

2. Verify Loki has data:
   ```bash
   curl "http://loki:3100/loki/api/v1/query?query={namespace=\"default\"}"
   ```

3. Check time range settings in config

### SLO Status Shows `no_data`

This means Prometheus has no `http_requests_total` metrics for your service. Your application needs to emit Prometheus metrics. Common solutions:
- Add a Prometheus client library to your service
- If using Istio or a service mesh, enable metrics collection
- Verify Prometheus has a scrape config for your service:
  ```bash
  kubectl get servicemonitor -n <your-namespace>
  ```

## Upgrading

```bash
helm repo update
helm upgrade k8s-telemetry-mcp k8s-telemetry-mcp/k8s-telemetry-mcp \
  --namespace monitoring \
  -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall k8s-telemetry-mcp --namespace monitoring
```
