# Changelog

All notable changes to K8s Telemetry MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-08-23

### Fixed
- `get_cluster_health` — PromQL queries used `count()` instead of `sum()` for pod phase metrics, causing all pods to appear as failed and pending
- `check_slo_status` — now returns `overall_status: no_data` with actionable setup instructions when no HTTP traffic metrics exist, instead of misleading `critical` status with burn_rate=720
- Tempo URL default corrected from port 3100 to 3200
- NetworkPolicy now allows port 443 egress for AWS Marketplace Metering Service endpoint
- Periodic `RegisterUsage` metering added — AWS Marketplace billing now recorded hourly, not just at startup
- `RegisterUsage` response signature verified on every call
- `MCP_LICENSE_CHECK_ENABLED` runtime toggle removed — license check is always enforced in production
- Helm liveness/readiness probes replaced with lightweight `find_spec` check instead of full module import
- Docker health check updated to match

### Added
- `src/tier.py` — tier enforcement module (Standard: 3 namespaces, Professional/Enterprise: unlimited)
- `MCP_MARKETPLACE_TIER` environment variable to configure subscription tier
- `MCP_LOCAL_DEV` flag for local development (bypasses license check)
- Cost disclaimer added to `get_resource_costs` tool output
- README prerequisites updated to mention log shipper requirement for Loki
- Deployment troubleshooting section for empty logs and `no_data` SLO status

## [1.0.0] - 2026-01-15

### Added
- Initial release of K8s Telemetry MCP Server
- **Loki Integration**
  - `query_pod_logs` - Query logs by pod name, namespace, container
  - `query_logs_custom` - Execute custom LogQL queries
- **Prometheus Integration**
  - `get_pod_metrics` - Get CPU, memory, restarts, network metrics
  - `query_prometheus` - Execute custom PromQL queries (instant and range)
  - `get_cluster_health` - Get cluster-wide health metrics
- **Tempo Integration**
  - `get_trace` - Retrieve trace by ID
  - `search_traces` - Search traces by service, operation, tags, duration
- **Analytics**
  - `analyze_logs` - Pattern detection, error categorization, recommendations
  - `build_incident_timeline` - Chronological correlation of logs, metrics, traces
  - `enrich_alert` - Full context gathering on alert fire with suggested queries
  - `get_resource_costs` - CPU/memory cost attribution by namespace with optimization hints
  - `check_slo_status` - SLO compliance, error budget remaining, burn rate, time to exhaustion
- **Enterprise Security**
  - Automatic PII/secret sanitization (credit cards, SSNs, AWS keys, JWTs, bearer tokens, DB connection strings, and more)
  - Non-root container execution
  - Read-only filesystem
  - NetworkPolicy restricting egress to observability stack only
  - Least-privilege ServiceAccount with no Kubernetes API permissions
  - Bytecode-only Docker image (source `.py` files removed at build time)
- **AWS Marketplace**
  - License enforcement via AWS Marketplace Metering Service `RegisterUsage` API
  - Metering event recorded on every server start for billing
- **Deployment**
  - Docker container image (python:3.11-slim base)
  - Helm chart for Kubernetes/EKS deployment with liveness and readiness probes
  - Configurable via `MCP_`-prefixed environment variables
- **Documentation**
  - Full README with step-by-step getting started guide
  - AWS Marketplace listing guide
  - Deployment and troubleshooting documentation
