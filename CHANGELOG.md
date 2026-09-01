# Changelog

All notable changes to K8s Telemetry MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.0.3 through 1.1.3 were never written up. The 1.2.0 entry below covers
> the correctness work; consult `git log` for the intervening feature releases.

## [1.2.6] - 2026-09-01

### Fixed

- **A denied or throttled CloudWatch metric call was indistinguishable from a metric with
  genuinely no datapoints in range.** Both silently produced an empty/missing entry, which
  is the same "no data returned" vs "nothing happened" conflation already fixed for
  CloudTrail and AWS Config in earlier releases, just not yet fixed for CloudWatch.
  `get_pod_metrics`, `get_cluster_health`, and the RDS/ElastiCache CloudWatch fallback now
  record an `error` (or `errors`, per-metric) key alongside `metrics` when any call fails,
  instead of leaving that metric out with no explanation.
- **RDS symptom detection had no way to see a burstable instance throttled to its
  baseline.** `CPUCreditBalance` is now requested alongside the existing RDS CloudWatch
  metrics. Confirmed live against a real `db.t4g.micro` under sustained load: query
  latency degraded from ~100ms to 5–12 seconds while `ReadLatency`, `WriteLatency` and even
  `CPUUtilization` all continued to read low — `CPUCreditBalance` was the only metric that
  showed the instance was in trouble. Absent on non-burstable instance classes, where it
  is simply omitted rather than reported as an error.
- **`get_recent_deployments` gave every rollout the same identity: a timestamp and an
  image tag.** When a Deployment is updated more than once inside the reconstruction
  window, two different rollouts became indistinguishable in the report. The Deployment
  controller's own `deployment.kubernetes.io/revision` annotation is now surfaced as
  `revision` on each entry — no extra API call, since Kubernetes already writes it on
  every rollout — and `fmt_recent_deployments` renders it alongside the name.
- Renamed `Promtops` to `KubeOpsAI` throughout the documentation (`README.md`,
  `CHANGELOG.md`, `SUPPORT.md`, `docs/DEPLOYMENT.md`) to match the product's actual name.
  `SUPPORT.md` also no longer describes paid tiers and a response-time SLA that applied to
  a commercial version of this server that does not exist — this project is free,
  open-source and community-support only; the commercial product built on it is a
  separate offering, linked but not merged in.

## [1.2.5] - 2026-08-30

### Fixed

- **`get_events` returned field names that no collector ever read, so every Kubernetes
  event was silently dropped from every reconstruction since this tool existed.** Found
  running a live end-to-end test against a real EKS cluster: a genuine `Unhealthy`
  readiness-probe event never appeared in a report even though the pod, node and AWS
  side of the same incident all did. The tool's own normalizer wrote `last_timestamp`
  and a nested `involved_object.name`/`involved_object.kind`; the real Kubernetes events
  API (and every downstream reader, in this repo and in KubeOpsAI) expected `last_time`
  and flat `object_name`/`object_kind`. This went undetected because the test fixtures
  for `get_events` were written to match the same wrong shape as the buggy code, so the
  tests passed while the feature never worked. Fixed the normalizer to emit the real
  field names, added a real Kubernetes event `uid` to the output (previously absent, so
  callers had no stable identifier to cite as evidence), and corrected
  `format.py::fmt_k8s_events`, which had the identical wrong field names and zero test
  coverage. Confirmed against a real EKS cluster: a `checkout-api` pod's `Unhealthy`
  event now appears in the rendered report with its real UID as the evidence reference.

- **The `PHONE` sanitizer pattern redacted EKS's own auto-generated resource name
  suffixes.** `eks-cluster-sg-kubeopsai-topology-test-1863340737` became
  `...-[REDACTED_PHONE]` because the pattern matched any bare run of 10 digits with all
  separators optional, and EKS appends a 10-digit numeric suffix to generated names.
  Fixed to require at least one delimiter between digit groups, so a real phone number
  (`555-123-4567`, `(555) 123-4567`) is still redacted while a bare 10-digit numeric
  suffix with no delimiters is not mistaken for one.

## [1.2.4] - 2026-08-30

### Added

- **`query_cloudtrail` and `get_resource_history` now report the resource type alongside
  each resource name.** Every CloudTrail event's `Resources` entry carries a `ResourceType`
  (confirmed against a real `RevokeSecurityGroupIngress` call: CloudTrail returned
  `AWS::EC2::SecurityGroup` alongside the security group id), and this was previously
  discarded — the `resources` field was a bare list of names, so nothing downstream could
  tell a security group id from an RDS instance id without already being told the type.
  A new `resource_details` field carries `{resource_type, resource_name}` pairs; the
  existing `resources` field is unchanged for backward compatibility. This is what lets
  KubeOpsAI discover which AWS resources to deepen an AWS Config lookup on automatically,
  without the caller already having to name them.

## [1.2.3] - 2026-08-30

### Fixed

- **The PII sanitizer redacted CIDR network addresses, corrupting the exact evidence a
  reconstruction reports on.** Found running `get_configuration_history` against a real
  account: revoking a security group's ingress rule for `10.0.0.0/8` produced a
  diff reading `[REDACTED_PRIVATE_IP]/8` instead of `10.0.0.0/8`. The `PRIVATE_IP` pattern
  matched any four-octet RFC1918-shaped string with no regard for a following `/NN` — and
  every real VPC uses an RFC1918 range, so this silently corrupted the flagship
  "security group tightened" scenario for nearly every customer's account, on the one
  field the report exists to preserve. A bare host IP with no CIDR suffix is still
  redacted; only the network-address form in a CIDR block is exempted.

## [1.2.2] - 2026-08-31

### Fixed

- **`aws_region` defaulted to `us-east-1`, silently overriding the AWS environment.**
  Found by running against a live account. boto3 already resolves a region from
  `AWS_REGION`, `AWS_DEFAULT_REGION`, the shared config file and instance metadata, and a
  hardcoded default shadowed all of it. On a cluster in any other region every CloudTrail,
  AWS Config, ECR and RDS query went to us-east-1 and returned nothing — and an empty
  result is indistinguishable from an account where nothing happened, so the tools reported
  a confident "no activity found" instead of an error.

  The default is now empty, which the clients pass through as `None` so boto3 performs its
  own resolution. Set `MCP_AWS_REGION` only to target a region other than the one the pod
  runs in, such as us-east-1 for global IAM and CloudFront events.

## [1.2.1] - 2026-08-31

### Added

- **Node-to-instance identity in `get_node_pressure`.** Each node entry now carries
  `provider_id` (the raw Kubernetes `spec.providerID`) and `instance_id` (the EC2
  instance id parsed from it). This is the only authoritative link between a Kubernetes
  node and the cloud instance behind it, and without it an AWS resource change cannot be
  connected to an in-cluster symptom by anything stronger than "happened at a similar
  time". Uses the `nodes` read permission the ClusterRole already grants — no RBAC
  change and no new tool, so the tool count remains 23.

### Removed

- **`ECSClient`, `EC2Client` and `LambdaClient` (`tools/ecs.py`, `tools/ec2.py`,
  `tools/lambda_.py`).** These were added, documented under an unreleased 1.3.0 heading,
  and then left on disk after the decision to revert them. They were registered as MCP
  tools nowhere, covered by no test, and exported from `tools/__init__.py`, so the
  package advertised a public API that could not be reached through the server and had
  never been exercised. The reasoning for not shipping them: CloudTrail already records
  every mutating API call regardless of service, and `get_configuration_history` is
  generic over `resource_type`, so these clients added only *symptom* sources at the cost
  of three clients, wider IAM, and a tool surface that shifts the product from
  Kubernetes-focused to generically AWS — which the in-cluster Helm deployment model does
  not fit. If they return, each should be justified by the specific symptom it surfaces
  (Lambda throttles, ECS stopped-task reasons, EC2 status-check failures).

## [1.2.0] - 2026-08-30

### Fixed

- **`query_cloudtrail` and `get_resource_history` returned arrays of `null`.**
  `_sanitize_event` built its result dict and never returned it, so both tools
  produced `{"events": [null, null, ...]}`. Present in every copy of the module. No
  test covered `tools/cloudtrail.py`, which is why it shipped.
- **`check_slo_status` burn rate was inflated by `720 / window_hours`** (30x at the
  default 24-hour window). The rate was divided by the window's fraction of a 30-day
  period even though `allowed_errors` already came from that window's own traffic.
  A service that had consumed exactly half its error budget reported a burn rate of
  15.0 and a "critical" status. The `burn_rate > 2` threshold therefore fired almost
  unconditionally.
- **`check_slo_status` reported an exhausted budget for healthy low-traffic services.**
  `int(total_requests * (1 - target))` truncated to 0 below ~1000 requests, so a
  service with zero errors was graded "exhausted"/"critical". Such windows now report
  `insufficient_traffic` with an explanation.
- **`check_slo_status` could contradict itself**, returning a "critical" error budget
  alongside an overall status of "healthy". `overall_status` is now the more severe of
  the SLO outcome and the budget outcome.
- **`build_incident_timeline` never emitted a metric event.** The code required an
  `anomaly` key that no backend sets. Anomalies are now derived from the data:
  non-zero restart counts, and points beyond a median/MAD threshold. Mean/stdev was
  unsuitable — a single large spike inflates both enough to hide behind them.
- **`enrich_alert` dropped every metric.** `_summarize_metrics` branched on scalars and
  dicts, but the server passes the `results` list from `get_pod_metrics`.
- **`enrich_alert` always reported zero recent deployments.** `AlertEnricher.enrich`
  accepts `recent_deployments`; the server never passed it, despite a bad rollout being
  the most common cause of a firing alert.
- **`get_resource_costs` roughly doubled every figure.** The PromQL summed cAdvisor's
  per-container series together with its pod-level aggregate. Now filtered with
  `container!="",image!=""`.
- **Redaction only descended one level.** Nested log payloads — a list of dicts, a dict
  inside a dict, a parsed JSON log line — reached the model unredacted. Replaced with a
  depth-bounded recursive walker.
- **The published package could not be imported.** `tools/alertmanager.py` had a
  `SyntaxError` from an under-indented `except`, introduced in 1.1.3. CI only linted and
  tested the parallel `src/` copy, so nothing caught it.
- **The Docker build could not have produced a working image.** It copied only `src/`
  while the console script pointed at `k8s_telemetry_mcp.server:main`.
- **Helm liveness and readiness probes could never fail.** They ran
  `importlib.util.find_spec('src')`, which reports whether a module is installed — true
  even with the process dead — and referenced the pre-rename package name.
- **The Helm Deployment ran the stdio server as its main process**, which exits on EOF
  when no stdin is attached (CrashLoopBackOff). Assistants connect via `kubectl exec`,
  which starts its own server process.
- `get_resource_history` mis-parsed ARNs whose resource part ends in a numeric
  qualifier (`...:function:my-fn:3` searched for `3`).

### Added

- **`get_configuration_history`** (23rd tool) — what actually changed on an AWS resource,
  with field-level before/after diffs. CloudTrail records that an API call happened; AWS
  Config records the resulting state, so this is the tool that shows a security group's
  ingress going from `10.0.0.0/8` to `0.0.0.0/0`. Each change carries a `config_item_id`
  and capture time for citation, plus AWS Config's `relationships` — factual resource
  adjacency rather than an inference. Diff paths are dotted with bracketed list indices
  (`ipPermissions[0].ipRanges[0]`), and the first snapshot in a window is reported as a
  baseline rather than a change.
- `validate_aws_resource_type` and `validate_aws_resource_id` validators. AWS Config
  resource types contain colons, which `validate_identifier` correctly rejects for
  Kubernetes names, so a dedicated validator was needed rather than loosening the
  existing one.
- `k8s-telemetry-mcp-host` console script (`keepalive.py`): the pod's long-lived
  process. Validates the installation at startup, logs the detected backends, and
  maintains a heartbeat file that the health probes check.
- `KubernetesClient.get_pod_status()` — pod phase, conditions and container states.
  Answers "is the dependency this service cannot reach actually running?". Moved here
  from kubeopsai, where it had been implemented against a non-existent attribute and
  could never succeed. Requires `get,list` on `pods`, which is beyond the ClusterRole
  this chart installs by default.
- `sanitize_structure()` for arbitrarily nested payloads.
- `query_cloudtrail` now reports `applied_filter`, `ignored_filters` and a
  `filter_note`. CloudTrail accepts one lookup attribute per call, so extra arguments
  used to be dropped silently while appearing to have been applied.
- Helm: Datadog credentials via `secretKeyRef` (`config.datadogExistingSecret`) instead
  of plaintext values visible in `helm get values`.
- Helm: `networkPolicy.allowAwsApiEgress` and `networkPolicy.awsEndpointCidrs` to
  scope or remove the broad port-443 egress rule.
- Tests for `tools/cloudtrail.py`, `sanitizers/`, `keepalive.py` and
  `KubernetesClient.get_pod_status` — none of which had any coverage. 199 → 341 tests.

### Changed

- **Consolidated the duplicated package.** `src/` and `k8s_telemetry_mcp/` were
  byte-identical apart from import prefixes, and only `src/` was tested or linted.
  `src/` has been removed; `k8s_telemetry_mcp/` is the single package.
- Helm NetworkPolicy: DNS egress scoped to `kube-system` rather than all namespaces.
- Helm: removed the `MCP_AWS_MARKETPLACE_PRODUCT_CODE`, `MCP_MARKETPLACE_TIER` and
  `MCP_LOCAL_DEV` environment variables, left over from before the project was
  open-sourced and read by nothing.
- Helm: `podSecurityContext` uid/gid 1000 → 1001, matching the image's `mcp` user.
- `values.yaml` now documents that the observability namespace must carry the label
  `name: monitoring`, without which the NetworkPolicy blocks every query.
- CI lints and tests the shipped package, runs a 3.11/3.12 matrix, and builds the
  image on pull requests so packaging breakage cannot reach a release tag.
- `config.VERSION` now tracks `pyproject.toml` (was reporting 1.1.1 at 1.1.3).

## [1.0.2] - 2026-08-23

### Added
- **Multi-backend support** — Datadog and CloudWatch backends auto-detected from environment variables. Set `MCP_DATADOG_API_KEY` for Datadog or `MCP_CLOUDWATCH_LOG_GROUP` for CloudWatch. Loki/Prometheus/Tempo remain the default.
- `src/tools/datadog.py` — Datadog Logs v2 and Metrics v1 client with PII sanitization
- `src/tools/cloudwatch.py` — CloudWatch Logs Insights and Container Insights client
- **Real tier differentiation** across all three tiers:
  - Standard ($29/mo): 1 namespace, 100 log lines max, core 8 tools only
  - Professional ($99/mo): unlimited namespaces, all 12 tools, 500 log lines, 24h range
  - Enterprise ($299/mo): unlimited namespaces, all 12 tools, 5000 log lines, 72h range
- `check_tool()` method on `TierEnforcer` — blocks analytics tools on Standard tier
- `max_log_lines` and `max_query_range_hours` properties on `TierEnforcer`
- `query_prometheus` and `get_resource_costs` return clear error when non-Prometheus backend is active
- `check_slo_status` returns clear error when non-Prometheus backend is active
- 33 new tests covering Datadog client, CloudWatch client, and tier differentiation

### Changed
- Server version bumped to `1.0.2`
- Backend clients initialized lazily via `_detect_backends()` at startup — no longer module-level globals
- `query_logs_custom` routes to `query_raw()` if backend supports it (Datadog/CloudWatch), otherwise falls back to `query_logs()`
- `build_incident_timeline` and `enrich_alert` gracefully skip traces if no trace backend configured
- Pricing updated: Standard $29/mo, Professional $99/mo, Enterprise $299/mo


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
