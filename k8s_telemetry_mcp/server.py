"""K8s Telemetry MCP Server — Community Edition.

Full 23-tool MCP server for Kubernetes observability.
Connect to Kiro, Claude Desktop, Amazon Q, or any MCP-compatible AI assistant.

All tools are fully unlocked. No license required.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from mcp.server import MCPServer

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.duration import validate_duration
from k8s_telemetry_mcp.logging_config import (
    generate_request_id,
    get_audit_logger,
    request_id_var,
    setup_logging,
)
from k8s_telemetry_mcp.tools import (
    AlertEnricher,
    AlertmanagerClient,
    AWSConfigClient,
    CloudTrailClient,
    CloudWatchClient,
    CostAnalyzer,
    DatabaseInsightsClient,
    DatadogClient,
    ECRClient,
    IncidentTimelineBuilder,
    KubernetesClient,
    LogAnalyzer,
    LokiClient,
    PrometheusClient,
    SLOChecker,
    TempoClient,
)
from k8s_telemetry_mcp.tools.format import (
    fmt_analyze_logs,
    fmt_build_incident_timeline,
    fmt_check_slo_status,
    fmt_enrich_alert,
    fmt_get_resource_costs,
    fmt_k8s_events,
    fmt_recent_deployments,
)
from k8s_telemetry_mcp.validation import (
    ValidationError,
    validate_aws_resource_id,
    validate_aws_resource_type,
    validate_identifier,
    validate_query,
    validate_trace_id,
)

logger = setup_logging(log_level=settings.log_level, json_format=settings.log_level != "DEBUG")
audit = get_audit_logger()

_log_client = None
_metrics_client = None
_trace_client = None
_cloudtrail_client: CloudTrailClient | None = None
_awsconfig_client: AWSConfigClient | None = None
_ecr_client: ECRClient | None = None
_database_client: DatabaseInsightsClient | None = None
_k8s_client: KubernetesClient | None = None
_alertmanager_client: AlertmanagerClient | None = None

log_analyzer = LogAnalyzer()
timeline_builder = IncidentTimelineBuilder()
alert_enricher = AlertEnricher()
cost_analyzer = CostAnalyzer()
slo_checker = SLOChecker()

mcp = MCPServer(settings.server_name)


def _detect_backends():
    if settings.datadog_api_key:
        logger.info("Backend: Datadog")
        dd = DatadogClient()
        return dd, dd, TempoClient() if settings.tempo_url else None
    if settings.cloudwatch_log_group:
        logger.info("Backend: CloudWatch")
        cw = CloudWatchClient()
        return cw, cw, TempoClient() if settings.tempo_url else None
    logger.info("Backend: Loki + Prometheus + Tempo")
    return (
        LokiClient() if settings.loki_url else None,
        PrometheusClient() if settings.prometheus_url else None,
        TempoClient() if settings.tempo_url else None,
    )


def _require_log_client():
    if _log_client is None:
        raise RuntimeError("No log backend configured. Set MCP_LOKI_URL, MCP_DATADOG_API_KEY, or MCP_CLOUDWATCH_LOG_GROUP.")
    return _log_client


def _require_metrics_client():
    if _metrics_client is None:
        raise RuntimeError("No metrics backend configured. Set MCP_PROMETHEUS_URL or MCP_DATADOG_API_KEY.")
    return _metrics_client


def _require_trace_client():
    if _trace_client is None:
        raise RuntimeError("No trace backend configured. Set MCP_TEMPO_URL.")
    return _trace_client


def _clamp_minutes(minutes: int) -> int:
    return max(1, min(minutes, settings.max_query_range_hours * 60))


def _clamp_lines(lines: int) -> int:
    return max(1, min(lines, settings.max_log_lines))


def _error_response(error: Exception, operation: str) -> str:
    if isinstance(error, ValidationError):
        return json.dumps({"error": f"Validation error: {error}"})
    if isinstance(error, httpx.ConnectError):
        return json.dumps({"error": "Connection failed: Unable to reach the observability backend."})
    if isinstance(error, httpx.TimeoutException):
        return json.dumps({"error": "Request timed out."})
    if isinstance(error, httpx.HTTPStatusError):
        return json.dumps({"error": f"HTTP {error.response.status_code}: {error.response.text[:200]}"})
    logger.error(f"{operation} failed: {error}")
    return json.dumps({"error": str(error)})


@mcp.tool()
async def query_pod_logs(
    pod_name: str,
    namespace: str = "default",
    container: str | None = None,
    timeframe_minutes: int = 60,
    limit: int = 100,
) -> str:
    """Query logs from a Kubernetes pod. Returns sanitized logs with PII/secrets redacted.

    Args:
        pod_name: Pod name or regex pattern (e.g. 'payment-service' or 'payment-.*')
        namespace: Kubernetes namespace
        container: Container name filter (optional)
        timeframe_minutes: How many minutes of logs to retrieve (1-1440)
        limit: Maximum number of log lines (1-500)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        pod_name = validate_identifier(pod_name, "pod_name")
        namespace = validate_identifier(namespace, "namespace")
        if container:
            container = validate_identifier(container, "container")
        now = datetime.now(UTC)
        logs = await _require_log_client().query_logs(
            pod_name=pod_name, namespace=namespace, container=container,
            start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)),
            end_time=now, limit=_clamp_lines(limit),
        )
        audit.log_tool_call("query_pod_logs", {"pod_name": pod_name}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(logs, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("query_pod_logs", {"pod_name": pod_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "query_pod_logs")


@mcp.tool()
async def query_logs_custom(
    query: str,
    timeframe_minutes: int = 60,
    limit: int = 100,
) -> str:
    """Execute a custom log query (LogQL for Loki, Datadog syntax, or CloudWatch Insights).

    Args:
        query: Backend-native query string
        timeframe_minutes: How many minutes of logs to retrieve (1-1440)
        limit: Maximum number of log lines (1-500)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        query = validate_query(query, "log query")
        now = datetime.now(UTC)
        client = _require_log_client()
        if hasattr(client, "query_raw"):
            logs = await client.query_raw(query, now - timedelta(minutes=_clamp_minutes(timeframe_minutes)), now)
        else:
            logs = await client.query_logs(query=query, start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)), end_time=now, limit=_clamp_lines(limit))
        audit.log_tool_call("query_logs_custom", {"query": query[:50]}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(logs, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("query_logs_custom", {"query": query[:50]}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "query_logs_custom")


@mcp.tool()
async def get_pod_metrics(
    pod_name: str,
    namespace: str = "default",
    metric_type: str = "cpu",
) -> str:
    """Get metrics for a Kubernetes pod (CPU, memory, restarts, network).

    Args:
        pod_name: Pod name or regex pattern
        namespace: Kubernetes namespace
        metric_type: One of: cpu, memory, restarts, network_rx, network_tx
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        pod_name = validate_identifier(pod_name, "pod_name")
        namespace = validate_identifier(namespace, "namespace")
        valid_types = ["cpu", "memory", "restarts", "network_rx", "network_tx"]
        if metric_type not in valid_types:
            raise ValidationError(f"metric_type must be one of: {valid_types}")
        result = await _require_metrics_client().get_pod_metrics(pod_name=pod_name, namespace=namespace, metric_type=metric_type)
        audit.log_tool_call("get_pod_metrics", {"pod_name": pod_name}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_pod_metrics", {"pod_name": pod_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_pod_metrics")


@mcp.tool()
async def query_prometheus(
    query: str,
    range_query: bool = False,
    timeframe_minutes: int = 60,
    step: str = "1m",
) -> str:
    """Execute a raw PromQL query against Prometheus.

    Args:
        query: PromQL expression
        range_query: Set to true for a time-series range query
        timeframe_minutes: Time range for range queries (1-1440)
        step: Step interval for range queries (e.g. '1m', '5m', '1h')
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        query = validate_query(query, "PromQL")
        client = _require_metrics_client()
        if not isinstance(client, PrometheusClient):
            return json.dumps({"error": "query_prometheus requires the Prometheus backend."})
        now = datetime.now(UTC)
        if range_query:
            results = await client.query_range(query=query, start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)), end_time=now, step=step)
        else:
            results = await client.query_instant(query)
        audit.log_tool_call("query_prometheus", {"query": query[:50]}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("query_prometheus", {"query": query[:50]}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "query_prometheus")


@mcp.tool()
async def get_cluster_health() -> str:
    """Get overall Kubernetes cluster health: node count, pod status, CPU/memory utilization."""
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        result = await _require_metrics_client().get_cluster_health()
        audit.log_tool_call("get_cluster_health", {}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_cluster_health", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_cluster_health")


@mcp.tool()
async def get_trace(trace_id: str) -> str:
    """Retrieve a distributed trace by ID from Tempo.

    Args:
        trace_id: Hexadecimal trace ID (16-32 characters)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        trace_id = validate_trace_id(trace_id)
        trace = await _require_trace_client().get_trace(trace_id)
        audit.log_tool_call("get_trace", {"trace_id": trace_id}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(trace, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_trace", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_trace")


@mcp.tool()
async def search_traces(
    service_name: str | None = None,
    operation: str | None = None,
    tags: dict[str, str] | None = None,
    min_duration: str | None = None,
    max_duration: str | None = None,
    timeframe_minutes: int = 60,
    limit: int = 20,
) -> str:
    """Search for distributed traces in Tempo by service, operation, or tags.

    Args:
        service_name: Filter by service name
        operation: Filter by operation name
        tags: Filter by span tags (key-value pairs)
        min_duration: Minimum trace duration (e.g. '100ms', '1s')
        max_duration: Maximum trace duration
        timeframe_minutes: How far back to search (1-1440)
        limit: Maximum traces to return (1-100)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        if service_name:
            service_name = validate_identifier(service_name, "service_name")
        if operation:
            operation = validate_identifier(operation, "operation")
        min_duration = validate_duration(min_duration, "min_duration")
        max_duration = validate_duration(max_duration, "max_duration")
        now = datetime.now(UTC)
        traces = await _require_trace_client().search_traces(
            service_name=service_name, operation=operation, tags=tags,
            min_duration=min_duration, max_duration=max_duration,
            start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)),
            end_time=now, limit=max(1, min(limit, 100)),
        )
        audit.log_tool_call("search_traces", {"service_name": service_name}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(traces, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("search_traces", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "search_traces")


@mcp.tool()
async def analyze_logs(
    service_name: str,
    namespace: str = "default",
    timeframe_minutes: int = 60,
    limit: int = 500,
) -> str:
    """Analyze logs for a service — detects errors, anomalies, and patterns with recommendations.

    Args:
        service_name: Service/pod name or regex pattern
        namespace: Kubernetes namespace
        timeframe_minutes: How many minutes of logs to analyze (1-1440)
        limit: Maximum log lines to analyze (1-500)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        service_name = validate_identifier(service_name, "service_name")
        namespace = validate_identifier(namespace, "namespace")
        now = datetime.now(UTC)
        logs = await _require_log_client().query_logs(
            pod_name=service_name, namespace=namespace,
            start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)),
            end_time=now, limit=_clamp_lines(limit),
        )
        analysis = log_analyzer.analyze(logs)
        analysis.update({"service": service_name, "namespace": namespace, "timeframe_minutes": timeframe_minutes})
        audit.log_tool_call("analyze_logs", {"service_name": service_name}, True, (time.perf_counter() - t0) * 1000)
        return fmt_analyze_logs(analysis)
    except Exception as e:
        audit.log_tool_call("analyze_logs", {"service_name": service_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "analyze_logs")


@mcp.tool()
async def build_incident_timeline(
    service_name: str,
    namespace: str = "default",
    timeframe_minutes: int = 60,
) -> str:
    """Build a chronological incident timeline combining logs, metrics, and traces.

    Args:
        service_name: Affected service name
        namespace: Kubernetes namespace
        timeframe_minutes: Time window to analyze (1-1440)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        service_name = validate_identifier(service_name, "service_name")
        namespace = validate_identifier(namespace, "namespace")
        now = datetime.now(UTC)
        start = now - timedelta(minutes=_clamp_minutes(timeframe_minutes))
        logs = await _require_log_client().query_logs(pod_name=service_name, namespace=namespace, start_time=start, end_time=now, limit=200)
        # Keyed by metric type so the timeline builder can attribute each anomaly.
        # Restarts matter most here: a non-zero count is a timeline event on its own.
        metrics: dict[str, Any] = {}
        for metric_type in ("cpu", "memory", "restarts"):
            try:
                metrics[metric_type] = await _require_metrics_client().get_pod_metrics(
                    pod_name=service_name, namespace=namespace, metric_type=metric_type
                )
            except Exception as metric_error:
                logger.debug(f"build_incident_timeline: {metric_type} unavailable: {metric_error}")
                metrics[metric_type] = None
        traces = await _require_trace_client().search_traces(service_name=service_name, start_time=start, end_time=now, limit=50) if _trace_client else []
        timeline = timeline_builder.build(logs, metrics, traces, service_name)
        timeline.update({"namespace": namespace, "timeframe_minutes": timeframe_minutes})
        audit.log_tool_call("build_incident_timeline", {"service_name": service_name}, True, (time.perf_counter() - t0) * 1000)
        return fmt_build_incident_timeline(timeline)
    except Exception as e:
        audit.log_tool_call("build_incident_timeline", {"service_name": service_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "build_incident_timeline")


@mcp.tool()
async def enrich_alert(
    alert_name: str,
    service_name: str,
    namespace: str = "default",
    timeframe_minutes: int = 30,
) -> str:
    """Enrich an alert with full context: recent logs, metrics, traces, and recommendations.

    Args:
        alert_name: Name of the alert that fired
        service_name: Affected service name
        namespace: Kubernetes namespace
        timeframe_minutes: Context window in minutes (1-60)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        service_name = validate_identifier(service_name, "service_name")
        namespace = validate_identifier(namespace, "namespace")
        now = datetime.now(UTC)
        start = now - timedelta(minutes=max(1, min(timeframe_minutes, 60)))
        logs = await _require_log_client().query_logs(pod_name=service_name, namespace=namespace, start_time=start, end_time=now, limit=100)
        metrics = {}
        for mt in ["cpu", "memory", "restarts"]:
            try:
                r = await _require_metrics_client().get_pod_metrics(pod_name=service_name, namespace=namespace, metric_type=mt)
                metrics[mt] = r.get("results", [])
            except Exception:
                metrics[mt] = None
        traces = await _require_trace_client().search_traces(service_name=service_name, start_time=start, end_time=now, limit=20) if _trace_client else []
        # A recent rollout is the single most common cause of a firing alert, and
        # AlertEnricher already accepts it — it was simply never passed, so the
        # "deployments" context was always empty.
        recent_deployments: list[dict] = []
        if _k8s_client is not None:
            try:
                deploy_result = await _k8s_client.get_recent_deployments(
                    namespace=namespace, timeframe_minutes=max(1, min(timeframe_minutes, 60))
                )
                recent_deployments = deploy_result.get("deployments", []) or []
            except Exception as deploy_error:
                logger.debug(f"enrich_alert: recent deployments unavailable: {deploy_error}")
        enriched = alert_enricher.enrich(
            alert_name, service_name, namespace, logs, metrics, traces,
            recent_deployments=recent_deployments,
        )
        enriched["timeframe_minutes"] = timeframe_minutes
        audit.log_tool_call("enrich_alert", {"alert_name": alert_name}, True, (time.perf_counter() - t0) * 1000)
        return fmt_enrich_alert(enriched)
    except Exception as e:
        audit.log_tool_call("enrich_alert", {"alert_name": alert_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "enrich_alert")


@mcp.tool()
async def get_resource_costs(
    namespace: str | None = None,
    timeframe_minutes: int = 60,
) -> str:
    """Get resource cost attribution by namespace with optimization suggestions.
    Note: Requires Prometheus backend. Cost estimates are approximate.

    Args:
        namespace: Filter by namespace (optional — omit for all namespaces)
        timeframe_minutes: Usage averaging window (1-1440)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        if namespace:
            namespace = validate_identifier(namespace, "namespace")
        client = _require_metrics_client()
        if not isinstance(client, PrometheusClient):
            return json.dumps({"error": "get_resource_costs requires the Prometheus backend."})
        # cAdvisor emits both per-container series and a pod-level aggregate with an
        # empty `container` label. Summing everything double-counted every pod, so the
        # reported cores, GB and dollar figures were roughly 2x actual usage.
        selectors = ['container!=""', 'image!=""']
        if namespace:
            selectors.insert(0, f'namespace="{namespace}"')
        metric_selector = ",".join(selectors)
        rate_window = _clamp_minutes(timeframe_minutes)
        cpu_results = await client.query_instant(f"sum(rate(container_cpu_usage_seconds_total{{{metric_selector}}}[{rate_window}m])) by (namespace)")
        mem_results = await client.query_instant(f"sum(container_memory_working_set_bytes{{{metric_selector}}}) by (namespace)")
        cpu_usage = {r.get("metric", {}).get("namespace", "unknown"): r.get("value", 0) or 0 for r in cpu_results}
        mem_usage = {r.get("metric", {}).get("namespace", "unknown"): r.get("value", 0) or 0 for r in mem_results}
        analysis = cost_analyzer.analyze(cpu_usage, mem_usage)
        analysis.update({
            "namespace_filter": namespace,
            "cost_disclaimer": "Cost estimates use approximate rates ($0.05/core-hour CPU, $0.01/GB-hour memory) for relative comparison only. Refer to AWS Cost Explorer for authoritative figures.",
        })
        audit.log_tool_call("get_resource_costs", {"namespace": namespace}, True, (time.perf_counter() - t0) * 1000)
        return fmt_get_resource_costs(analysis)
    except Exception as e:
        audit.log_tool_call("get_resource_costs", {"namespace": namespace}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_resource_costs")


@mcp.tool()
async def check_slo_status(
    service_name: str,
    namespace: str = "default",
    availability_target: float = 0.999,
    latency_target_ms: float = 500.0,
    latency_percentile: float = 0.99,
    window_hours: int = 24,
) -> str:
    """Check SLO compliance — error budgets, burn rate, and availability vs. latency targets.

    Args:
        service_name: Service to check
        namespace: Kubernetes namespace
        availability_target: Target availability (0.0-1.0, default 99.9%)
        latency_target_ms: Target latency in milliseconds
        latency_percentile: Latency percentile to measure (default p99)
        window_hours: Measurement window in hours (1-720)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        service_name = validate_identifier(service_name, "service_name")
        namespace = validate_identifier(namespace, "namespace")
        client = _require_metrics_client()
        if not isinstance(client, PrometheusClient):
            return json.dumps({"error": "check_slo_status requires the Prometheus backend."})
        window_hours = max(1, min(720, window_hours))
        total_results = await client.query_instant(f'sum(increase(http_requests_total{{namespace="{namespace}",service=~"{service_name}.*"}}[{window_hours}h]))')
        error_results = await client.query_instant(f'sum(increase(http_requests_total{{namespace="{namespace}",service=~"{service_name}.*",status=~"5.."}}[{window_hours}h]))')
        latency_results = await client.query_instant(f'histogram_quantile({latency_percentile}, sum(rate(http_request_duration_seconds_bucket{{namespace="{namespace}",service=~"{service_name}.*"}}[{window_hours}h])) by (le))')
        total_requests = int(total_results[0].get("value", 0) or 0) if total_results else 0
        error_count = int(error_results[0].get("value", 0) or 0) if error_results else 0
        current_latency_ms = (latency_results[0].get("value", 0) or 0) * 1000 if latency_results else 0
        current_availability = 1.0 - (error_count / max(total_requests, 1))
        result = slo_checker.check(
            service_name=service_name, availability_target=max(0.0, min(1.0, availability_target)),
            latency_target_ms=max(1.0, latency_target_ms), latency_percentile=max(0.5, min(1.0, latency_percentile)),
            current_availability=current_availability, current_latency_ms=current_latency_ms,
            error_count=error_count, total_requests=total_requests, window_hours=window_hours,
        )
        result["namespace"] = namespace
        audit.log_tool_call("check_slo_status", {"service_name": service_name}, True, (time.perf_counter() - t0) * 1000)
        return fmt_check_slo_status(result)
    except Exception as e:
        audit.log_tool_call("check_slo_status", {"service_name": service_name}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "check_slo_status")


@mcp.tool()
async def query_cloudtrail(
    keyword: str | None = None,
    event_name: str | None = None,
    username: str | None = None,
    resource_name: str | None = None,
    timeframe_minutes: int = 60,
    limit: int = 50,
) -> str:
    """Search AWS CloudTrail events by keyword, event name, username, or resource.
    Requires cloudtrail:LookupEvents IAM permission.

    Args:
        keyword: Search term matched against event name
        event_name: Exact CloudTrail event name (e.g. 'DeleteDeployment')
        username: Filter by IAM username or role session name
        resource_name: Filter by AWS resource name or ID
        timeframe_minutes: How far back to search (1-1440)
        limit: Maximum events to return (1-50)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        now = datetime.now(UTC)
        result = await _cloudtrail_client.query_events(
            keyword=keyword, event_name=event_name, username=username, resource_name=resource_name,
            start_time=now - timedelta(minutes=_clamp_minutes(timeframe_minutes)), end_time=now,
            limit=max(1, min(limit, 50)),
        )
        audit.log_tool_call("query_cloudtrail", {"keyword": keyword}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("query_cloudtrail", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "query_cloudtrail")


@mcp.tool()
async def get_resource_history(
    resource_id: str,
    timeframe_days: int = 90,
    limit: int = 50,
) -> str:
    """Get the full CloudTrail audit trail for a specific AWS resource.
    Shows who created, modified, and deleted it.
    Requires cloudtrail:LookupEvents IAM permission.

    Args:
        resource_id: AWS resource ID or ARN
        timeframe_days: How many days back to search (1-90)
        limit: Maximum events to return (1-50)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        now = datetime.now(UTC)
        result = await _cloudtrail_client.get_resource_history(
            resource_id=resource_id,
            start_time=now - timedelta(days=max(1, min(timeframe_days, 90))), end_time=now,
            limit=max(1, min(limit, 50)),
        )
        audit.log_tool_call("get_resource_history", {"resource_id": resource_id[:20]}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_resource_history", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_resource_history")


@mcp.tool()
async def get_configuration_history(
    resource_type: str,
    resource_id: str,
    timeframe_hours: int = 24,
    limit: int = 20,
) -> str:
    """Get the configuration-change history for one AWS resource, with field-level diffs.

    Answers "what actually changed on this resource, and when". CloudTrail records that
    an API call happened; AWS Config records the resulting state, so this is what tells
    you a security group's ingress rules went from one value to another.

    Each change carries a capture time and a config_item_id for citation, plus the
    resources AWS Config considers related — useful for establishing whether a changed
    resource is actually connected to a failing one.

    Requires AWS Config to be enabled and config:GetResourceConfigHistory.

    Args:
        resource_type: AWS Config resource type, e.g. 'AWS::EC2::SecurityGroup'
        resource_id: Resource ID, e.g. 'sg-0123456789abcdef0'
        timeframe_hours: How far back to look (1-720)
        limit: Maximum configuration snapshots to retrieve (1-100)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        resource_type = validate_aws_resource_type(resource_type)
        resource_id = validate_aws_resource_id(resource_id)
        now = datetime.now(UTC)
        result = await _awsconfig_client.get_configuration_history(
            resource_type=resource_type,
            resource_id=resource_id,
            start_time=now - timedelta(hours=max(1, min(timeframe_hours, 720))),
            end_time=now,
            limit=max(1, min(limit, 100)),
        )
        audit.log_tool_call("get_configuration_history", {"resource_id": resource_id[:40]}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_configuration_history", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_configuration_history")


@mcp.tool()
async def get_resource_compliance(
    resource_id: str | None = None,
    resource_type: str | None = None,
    compliance_filter: str | None = None,
) -> str:
    """Get AWS Config compliance status for resources. Shows drift and non-compliant rules.
    Requires AWS Config to be enabled and config:Describe* IAM permissions.

    Args:
        resource_id: Specific resource ID (optional — omit for all rules summary)
        resource_type: AWS resource type e.g. 'AWS::EC2::Instance' (optional)
        compliance_filter: Filter by COMPLIANT, NON_COMPLIANT, or NOT_APPLICABLE
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        result = await _awsconfig_client.get_resource_compliance(
            resource_id=resource_id, resource_type=resource_type, compliance_filter=compliance_filter,
        )
        audit.log_tool_call("get_resource_compliance", {"resource_id": resource_id}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_resource_compliance", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_resource_compliance")


@mcp.tool()
async def get_image_vulnerabilities(
    repository_name: str,
    image_tag: str = "latest",
    severity_filter: list[str] | None = None,
) -> str:
    """Get vulnerability findings for a container image in ECR.
    Uses AWS Inspector v2 if enabled, falls back to ECR basic scan.
    Requires ecr:DescribeImageScanFindings and inspector2:ListFindings IAM permissions.

    Args:
        repository_name: ECR repository name
        image_tag: Image tag to check (default: latest)
        severity_filter: Severities to include e.g. ['CRITICAL', 'HIGH']
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        result = await _ecr_client.get_image_vulnerabilities(
            repository_name=repository_name, image_tag=image_tag, severity_filter=severity_filter,
        )
        audit.log_tool_call("get_image_vulnerabilities", {"repository": repository_name}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_image_vulnerabilities", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_image_vulnerabilities")


@mcp.tool()
async def get_database_insights(
    db_identifier: str,
    db_type: str = "rds",
    timeframe_minutes: int = 60,
) -> str:
    """Get database performance insights for RDS or ElastiCache.

    Args:
        db_identifier: RDS instance/cluster ID or ElastiCache cluster ID
        db_type: 'rds' or 'elasticache'
        timeframe_minutes: Time window to analyze (1-1440)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        if db_type not in ("rds", "elasticache"):
            return json.dumps({"error": "db_type must be 'rds' or 'elasticache'"})
        result = await _database_client.get_database_insights(
            db_identifier=db_identifier, db_type=db_type,
            timeframe_minutes=_clamp_minutes(timeframe_minutes),
        )
        audit.log_tool_call("get_database_insights", {"db_identifier": db_identifier}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_database_insights", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_database_insights")


@mcp.tool()
async def get_k8s_events(
    namespace: str = "default",
    pod_name: str | None = None,
    event_type: str | None = None,
    timeframe_minutes: int = 60,
    limit: int = 100,
) -> str:
    """Get Kubernetes events for a namespace or pod.
    Answers 'why is my pod pending/OOMKilled/CrashLoopBackOff?'
    Requires rbac.enabled=true in Helm values.

    Args:
        namespace: Kubernetes namespace
        pod_name: Filter events for a specific pod (optional)
        event_type: Filter by 'Warning' or 'Normal' (optional)
        timeframe_minutes: How far back to look (1-1440)
        limit: Maximum events to return (1-500)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        namespace = validate_identifier(namespace, "namespace")
        if pod_name:
            pod_name = validate_identifier(pod_name, "pod_name")
        if event_type and event_type not in ("Warning", "Normal"):
            return json.dumps({"error": "event_type must be 'Warning' or 'Normal'"})
        if _k8s_client is None:
            return json.dumps({"error": "Kubernetes API client unavailable. Check server startup logs."})
        result = await _k8s_client.get_events(
            namespace=namespace, pod_name=pod_name, event_type=event_type,
            timeframe_minutes=_clamp_minutes(timeframe_minutes), limit=max(1, min(limit, 500)),
        )
        audit.log_tool_call("get_k8s_events", {"namespace": namespace}, True, (time.perf_counter() - t0) * 1000)
        return fmt_k8s_events(result)
    except Exception as e:
        audit.log_tool_call("get_k8s_events", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_k8s_events")


@mcp.tool()
async def get_scaling_history(
    namespace: str = "default",
    deployment_name: str | None = None,
    timeframe_minutes: int = 60,
) -> str:
    """Get HPA scaling history and current autoscaler status for deployments.
    Requires kube-state-metrics for historical data.

    Args:
        namespace: Kubernetes namespace
        deployment_name: Filter to a specific deployment (optional)
        timeframe_minutes: How far back to look (1-1440)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        namespace = validate_identifier(namespace, "namespace")
        if deployment_name:
            deployment_name = validate_identifier(deployment_name, "deployment_name")
        if _k8s_client is None:
            return json.dumps({"error": "Kubernetes API client unavailable."})
        result = await _k8s_client.get_scaling_history(
            namespace=namespace, deployment_name=deployment_name,
            timeframe_minutes=_clamp_minutes(timeframe_minutes),
        )
        audit.log_tool_call("get_scaling_history", {"namespace": namespace}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_scaling_history", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_scaling_history")


@mcp.tool()
async def get_node_pressure() -> str:
    """Get Kubernetes node pressure conditions, resource capacity, and eviction status.
    Identifies nodes with MemoryPressure, DiskPressure, or PIDPressure.
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        if _k8s_client is None:
            return json.dumps({"error": "Kubernetes API client unavailable."})
        result = await _k8s_client.get_node_pressure()
        audit.log_tool_call("get_node_pressure", {}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_node_pressure", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_node_pressure")


@mcp.tool()
async def get_alertmanager_history(
    service_name: str | None = None,
    namespace: str | None = None,
    timeframe_minutes: int = 60,
    include_silences: bool = True,
) -> str:
    """Get recent alerts and active silences from Prometheus Alertmanager.
    Requires MCP_ALERTMANAGER_URL to be configured.

    Args:
        service_name: Filter alerts by service name (optional)
        namespace: Filter alerts by namespace label (optional)
        timeframe_minutes: How far back to look (1-1440)
        include_silences: Whether to include active silences (default: true)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        if _alertmanager_client is None:
            return json.dumps({"error": "Alertmanager not configured. Set MCP_ALERTMANAGER_URL."})
        result = await _alertmanager_client.get_alert_history(
            service_name=service_name, namespace=namespace,
            timeframe_minutes=_clamp_minutes(timeframe_minutes), include_silences=include_silences,
        )
        audit.log_tool_call("get_alertmanager_history", {"service_name": service_name}, True, (time.perf_counter() - t0) * 1000)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        audit.log_tool_call("get_alertmanager_history", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_alertmanager_history")


@mcp.tool()
async def get_recent_deployments(
    namespace: str = "default",
    timeframe_minutes: int = 60,
) -> str:
    """Get recent Kubernetes deployment changes — what was rolled out in the last N minutes.
    Useful for correlating deployments with incidents.

    Args:
        namespace: Kubernetes namespace
        timeframe_minutes: How far back to look (1-1440)
    """
    request_id_var.set(generate_request_id())
    t0 = time.perf_counter()
    try:
        namespace = validate_identifier(namespace, "namespace")
        if _k8s_client is None:
            return json.dumps({"error": "Kubernetes API client unavailable."})
        result = await _k8s_client.get_recent_deployments(
            namespace=namespace, timeframe_minutes=_clamp_minutes(timeframe_minutes),
        )
        audit.log_tool_call("get_recent_deployments", {"namespace": namespace}, True, (time.perf_counter() - t0) * 1000)
        return fmt_recent_deployments(result)
    except Exception as e:
        audit.log_tool_call("get_recent_deployments", {}, False, (time.perf_counter() - t0) * 1000, str(e))
        return _error_response(e, "get_recent_deployments")


def main():
    """Entry point."""
    logger.info(f"Starting {settings.server_name} v{settings.server_version} (community edition)")
    asyncio.run(_run())


async def _run():
    global _log_client, _metrics_client, _trace_client
    global _cloudtrail_client, _awsconfig_client, _ecr_client
    global _database_client, _k8s_client, _alertmanager_client

    _log_client, _metrics_client, _trace_client = _detect_backends()

    _cloudtrail_client = CloudTrailClient()
    _awsconfig_client = AWSConfigClient()
    _ecr_client = ECRClient()
    _database_client = DatabaseInsightsClient()
    _alertmanager_client = AlertmanagerClient() if settings.alertmanager_url else None

    try:
        _k8s_client = KubernetesClient()
        logger.info("Kubernetes API client initialized")
    except Exception as e:
        logger.warning(f"Kubernetes API client unavailable: {e}")
        _k8s_client = None

    await mcp.run_stdio_async()


if __name__ == "__main__":
    main()
