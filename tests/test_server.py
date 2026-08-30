"""Tests for all 22 MCP server tools."""

import json
from unittest.mock import patch

import httpx
import pytest

import k8s_telemetry_mcp.server as server_module
from k8s_telemetry_mcp.server import (
    analyze_logs,
    build_incident_timeline,
    check_slo_status,
    enrich_alert,
    get_alertmanager_history,
    get_cluster_health,
    get_database_insights,
    get_image_vulnerabilities,
    get_k8s_events,
    get_node_pressure,
    get_pod_metrics,
    get_recent_deployments,
    get_resource_compliance,
    get_resource_costs,
    get_resource_history,
    get_scaling_history,
    get_trace,
    query_cloudtrail,
    query_logs_custom,
    query_pod_logs,
    query_prometheus,
    search_traces,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ok(result: str) -> dict:
    """Parse JSON result and assert no error key."""
    data = json.loads(result)
    assert "error" not in data, f"Unexpected error: {data.get('error')}"
    return data


def err(result: str) -> str:
    data = json.loads(result)
    assert "error" in data
    return data["error"]


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestQueryPodLogs:
    async def test_happy_path(self, mock_loki):
        result = await query_pod_logs("my-pod", "default")
        data = json.loads(result)
        assert isinstance(data, list)
        mock_loki.query_logs.assert_called_once()

    async def test_with_container(self, mock_loki):
        result = await query_pod_logs("my-pod", "default", container="app")
        assert "error" not in json.loads(result)

    async def test_invalid_pod_name(self):
        result = await query_pod_logs("-bad-pod", "default")
        assert "error" in json.loads(result)

    async def test_invalid_namespace(self):
        result = await query_pod_logs("pod", "-bad-ns")
        assert "error" in json.loads(result)

    async def test_no_log_client(self):
        with patch.object(server_module, "_log_client", None):
            result = await query_pod_logs("pod", "default")
            assert "error" in json.loads(result)

    async def test_limit_clamped(self, mock_loki):
        await query_pod_logs("pod", "default", limit=99999)
        call_kwargs = mock_loki.query_logs.call_args.kwargs
        assert call_kwargs["limit"] <= 500

    async def test_timeframe_clamped(self, mock_loki):
        await query_pod_logs("pod", "default", timeframe_minutes=99999)
        mock_loki.query_logs.assert_called_once()

    async def test_connect_error_returns_error_json(self, mock_loki):
        mock_loki.query_logs.side_effect = httpx.ConnectError("refused")
        result = await query_pod_logs("pod", "default")
        assert "error" in json.loads(result)

    async def test_timeout_returns_error_json(self, mock_loki):
        mock_loki.query_logs.side_effect = httpx.TimeoutException("timeout")
        result = await query_pod_logs("pod", "default")
        assert "error" in json.loads(result)

    async def test_regex_pod_name_allowed(self, mock_loki):
        result = await query_pod_logs("payment-.*", "default")
        assert "error" not in json.loads(result)


@pytest.mark.asyncio
class TestQueryLogsCustom:
    async def test_happy_path(self, mock_loki):
        result = await query_logs_custom('{app="test"} |= "error"')
        assert "error" not in json.loads(result)

    async def test_uses_query_raw_if_available(self, mock_loki):
        await query_logs_custom("my query")
        mock_loki.query_raw.assert_called_once()

    async def test_empty_query_raises(self):
        result = await query_logs_custom("")
        assert "error" in json.loads(result)

    async def test_injection_rejected(self):
        result = await query_logs_custom("up; drop table foo")
        assert "error" in json.loads(result)

    async def test_no_log_client(self):
        with patch.object(server_module, "_log_client", None):
            result = await query_logs_custom('{app="x"}')
            assert "error" in json.loads(result)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetPodMetrics:
    async def test_cpu(self, mock_prometheus):
        result = await get_pod_metrics("pod", "default", "cpu")
        ok(result)
        mock_prometheus.get_pod_metrics.assert_called_once()

    async def test_memory(self, mock_prometheus):
        result = await get_pod_metrics("pod", "default", "memory")
        ok(result)

    async def test_invalid_metric_type(self):
        result = await get_pod_metrics("pod", "default", "invalid")
        assert "error" in json.loads(result)

    async def test_invalid_pod_name(self):
        result = await get_pod_metrics("-bad", "default", "cpu")
        assert "error" in json.loads(result)

    async def test_no_metrics_client(self):
        with patch.object(server_module, "_metrics_client", None):
            result = await get_pod_metrics("pod", "default", "cpu")
            assert "error" in json.loads(result)

    @pytest.mark.parametrize("metric_type", ["cpu", "memory", "restarts", "network_rx", "network_tx"])
    async def test_all_valid_metric_types(self, mock_prometheus, metric_type):
        result = await get_pod_metrics("pod", "default", metric_type)
        assert "error" not in json.loads(result)


@pytest.mark.asyncio
class TestQueryPrometheus:
    async def test_instant_query(self, mock_prometheus):
        result = await query_prometheus("up")
        ok(result)
        mock_prometheus.query_instant.assert_called_once_with("up")

    async def test_range_query(self, mock_prometheus):
        result = await query_prometheus("up", range_query=True, timeframe_minutes=30)
        ok(result)
        mock_prometheus.query_range.assert_called_once()

    async def test_empty_query_rejected(self):
        result = await query_prometheus("")
        assert "error" in json.loads(result)

    async def test_non_prometheus_backend_rejected(self, mock_loki):
        with patch.object(server_module, "_metrics_client", mock_loki):
            result = await query_prometheus("up")
            assert "error" in json.loads(result)

    async def test_injection_rejected(self):
        result = await query_prometheus("up; drop table")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetClusterHealth:
    async def test_happy_path(self, mock_prometheus):
        result = await get_cluster_health()
        ok(result)

    async def test_no_metrics_client(self):
        with patch.object(server_module, "_metrics_client", None):
            result = await get_cluster_health()
            assert "error" in json.loads(result)


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetTrace:
    async def test_happy_path(self, mock_tempo):
        result = await get_trace("abcdef1234567890")
        ok(result)
        mock_tempo.get_trace.assert_called_once_with("abcdef1234567890")

    async def test_invalid_trace_id(self):
        result = await get_trace("not-a-trace-id")
        assert "error" in json.loads(result)

    async def test_empty_trace_id(self):
        result = await get_trace("")
        assert "error" in json.loads(result)

    async def test_no_trace_client(self):
        with patch.object(server_module, "_trace_client", None):
            result = await get_trace("abcdef1234567890")
            assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestSearchTraces:
    async def test_happy_path(self, mock_tempo):
        result = await search_traces(service_name="my-svc")
        data = json.loads(result)
        assert isinstance(data, list)

    async def test_no_trace_client(self):
        with patch.object(server_module, "_trace_client", None):
            result = await search_traces(service_name="svc")
            assert "error" in json.loads(result)

    async def test_invalid_service_name(self):
        result = await search_traces(service_name="-bad")
        assert "error" in json.loads(result)

    async def test_invalid_duration(self):
        result = await search_traces(min_duration="bad-duration")
        assert "error" in json.loads(result)

    async def test_limit_clamped(self, mock_tempo):
        await search_traces(limit=9999)
        call_kwargs = mock_tempo.search_traces.call_args.kwargs
        assert call_kwargs["limit"] <= 100


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAnalyzeLogs:
    async def test_happy_path(self, mock_loki):
        result = await analyze_logs("my-svc", "default")
        assert "my-svc" in result
        assert isinstance(result, str)

    async def test_invalid_service_name(self):
        result = await analyze_logs("-bad", "default")
        assert "error" in json.loads(result)

    async def test_no_log_client(self):
        with patch.object(server_module, "_log_client", None):
            result = await analyze_logs("svc", "default")
            assert "error" in json.loads(result)

    async def test_service_in_result(self, mock_loki):
        result = await analyze_logs("my-svc", "default")
        assert "my-svc" in result


@pytest.mark.asyncio
class TestBuildIncidentTimeline:
    async def test_happy_path(self, mock_loki, mock_prometheus, mock_tempo):
        result = await build_incident_timeline("my-svc", "default")
        assert "my-svc" in result
        assert isinstance(result, str)

    async def test_upsell_present(self, mock_loki, mock_prometheus, mock_tempo):
        result = await build_incident_timeline("my-svc", "default")
        assert "kubeopsai.net" in result

    async def test_no_log_client(self):
        with patch.object(server_module, "_log_client", None):
            result = await build_incident_timeline("svc", "default")
            assert "error" in json.loads(result)

    async def test_invalid_service_name(self):
        result = await build_incident_timeline("-bad", "default")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestEnrichAlert:
    async def test_happy_path(self, mock_loki, mock_prometheus, mock_tempo):
        result = await enrich_alert("HighCPU", "my-svc", "default")
        assert "HighCPU" in result
        assert isinstance(result, str)

    async def test_upsell_present(self, mock_loki, mock_prometheus, mock_tempo):
        result = await enrich_alert("HighCPU", "my-svc", "default")
        assert "kubeopsai.net" in result

    async def test_invalid_service_name(self):
        result = await enrich_alert("Alert", "-bad", "default")
        assert "error" in json.loads(result)

    async def test_no_log_client(self):
        with patch.object(server_module, "_log_client", None):
            result = await enrich_alert("Alert", "svc", "default")
            assert "error" in json.loads(result)

    async def test_timeframe_clamped_to_60(self, mock_loki, mock_prometheus):
        result = await enrich_alert("Alert", "svc", "default", timeframe_minutes=9999)
        assert "error" not in result or "kubeopsai.net" in result


@pytest.mark.asyncio
class TestGetResourceCosts:
    async def test_happy_path(self, mock_prometheus):
        result = await get_resource_costs()
        assert "$" in result
        assert isinstance(result, str)

    async def test_with_namespace_filter(self, mock_prometheus):
        result = await get_resource_costs(namespace="default")
        assert isinstance(result, str)

    async def test_non_prometheus_backend(self, mock_loki):
        with patch.object(server_module, "_metrics_client", mock_loki):
            result = await get_resource_costs()
            assert "error" in json.loads(result)

    async def test_invalid_namespace(self):
        result = await get_resource_costs(namespace="-bad")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestCheckSloStatus:
    async def test_happy_path(self, mock_prometheus):
        result = await check_slo_status("my-svc", "default")
        assert "my-svc" in result
        assert isinstance(result, str)

    async def test_non_prometheus_backend(self, mock_loki):
        with patch.object(server_module, "_metrics_client", mock_loki):
            result = await check_slo_status("svc", "default")
            assert "error" in json.loads(result)

    async def test_invalid_service_name(self):
        result = await check_slo_status("-bad", "default")
        assert "error" in json.loads(result)

    async def test_window_hours_clamped(self, mock_prometheus):
        result = await check_slo_status("svc", "default", window_hours=9999)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetK8sEvents:
    async def test_happy_path(self, mock_k8s):
        result = await get_k8s_events("default")
        assert isinstance(result, str)
        mock_k8s.get_events.assert_called_once()

    async def test_with_pod_filter(self, mock_k8s):
        result = await get_k8s_events("default", pod_name="my-pod")
        assert isinstance(result, str)

    async def test_invalid_event_type(self):
        result = await get_k8s_events("default", event_type="Invalid")
        assert "error" in json.loads(result)

    async def test_valid_event_types(self, mock_k8s):
        for et in ("Warning", "Normal"):
            result = await get_k8s_events("default", event_type=et)
            assert "error" not in result or "Kubernetes" in result

    async def test_no_k8s_client(self):
        with patch.object(server_module, "_k8s_client", None):
            result = await get_k8s_events("default")
            assert "error" in json.loads(result)

    async def test_invalid_namespace(self):
        result = await get_k8s_events("-bad")
        assert "error" in json.loads(result)

    async def test_limit_clamped(self, mock_k8s):
        await get_k8s_events("default", limit=9999)
        call_kwargs = mock_k8s.get_events.call_args.kwargs
        assert call_kwargs["limit"] <= 500


@pytest.mark.asyncio
class TestGetScalingHistory:
    async def test_happy_path(self, mock_k8s):
        result = await get_scaling_history("default")
        ok(result)

    async def test_with_deployment_filter(self, mock_k8s):
        result = await get_scaling_history("default", deployment_name="my-deploy")
        ok(result)

    async def test_no_k8s_client(self):
        with patch.object(server_module, "_k8s_client", None):
            result = await get_scaling_history("default")
            assert "error" in json.loads(result)

    async def test_invalid_namespace(self):
        result = await get_scaling_history("-bad")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetNodePressure:
    async def test_happy_path(self, mock_k8s):
        result = await get_node_pressure()
        ok(result)

    async def test_no_k8s_client(self):
        with patch.object(server_module, "_k8s_client", None):
            result = await get_node_pressure()
            assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetRecentDeployments:
    async def test_happy_path(self, mock_k8s):
        result = await get_recent_deployments("default")
        assert isinstance(result, str)

    async def test_no_k8s_client(self):
        with patch.object(server_module, "_k8s_client", None):
            result = await get_recent_deployments("default")
            assert "error" in json.loads(result)

    async def test_invalid_namespace(self):
        result = await get_recent_deployments("-bad")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetAlertmanagerHistory:
    async def test_happy_path(self, mock_alertmanager):
        result = await get_alertmanager_history()
        ok(result)

    async def test_no_alertmanager_client(self):
        with patch.object(server_module, "_alertmanager_client", None):
            result = await get_alertmanager_history()
            assert "error" in json.loads(result)

    async def test_with_filters(self, mock_alertmanager):
        result = await get_alertmanager_history(service_name="svc", namespace="default")
        ok(result)


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestQueryCloudtrail:
    async def test_happy_path(self, mock_cloudtrail):
        result = await query_cloudtrail(keyword="DeleteDeployment")
        ok(result)

    async def test_by_event_name(self, mock_cloudtrail):
        result = await query_cloudtrail(event_name="RunInstances")
        ok(result)

    async def test_by_username(self, mock_cloudtrail):
        result = await query_cloudtrail(username="admin")
        ok(result)

    async def test_limit_clamped(self, mock_cloudtrail):
        await query_cloudtrail(limit=9999)
        call_kwargs = mock_cloudtrail.query_events.call_args.kwargs
        assert call_kwargs["limit"] <= 50

    async def test_backend_error_returns_error_json(self, mock_cloudtrail):
        mock_cloudtrail.query_events.side_effect = Exception("AWS error")
        result = await query_cloudtrail()
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetResourceHistory:
    async def test_happy_path(self, mock_cloudtrail):
        result = await get_resource_history("i-1234567890abcdef0")
        ok(result)

    async def test_timeframe_clamped(self, mock_cloudtrail):
        result = await get_resource_history("i-1234567890abcdef0", timeframe_days=9999)
        ok(result)
        call_kwargs = mock_cloudtrail.get_resource_history.call_args.kwargs
        # start_time should be within 90 days
        from datetime import UTC, datetime
        now = datetime.now(UTC)
        assert (now - call_kwargs["start_time"]).days <= 90


@pytest.mark.asyncio
class TestGetResourceCompliance:
    async def test_happy_path(self, mock_awsconfig):
        result = await get_resource_compliance()
        ok(result)

    async def test_with_filters(self, mock_awsconfig):
        result = await get_resource_compliance(
            resource_id="i-123", resource_type="AWS::EC2::Instance",
            compliance_filter="NON_COMPLIANT"
        )
        ok(result)

    async def test_backend_error(self, mock_awsconfig):
        mock_awsconfig.get_resource_compliance.side_effect = Exception("Config error")
        result = await get_resource_compliance()
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetImageVulnerabilities:
    async def test_happy_path(self, mock_ecr):
        result = await get_image_vulnerabilities("my-repo")
        ok(result)

    async def test_with_tag_and_filter(self, mock_ecr):
        result = await get_image_vulnerabilities("my-repo", "v1.0.0", ["CRITICAL", "HIGH"])
        ok(result)

    async def test_backend_error(self, mock_ecr):
        mock_ecr.get_image_vulnerabilities.side_effect = Exception("ECR error")
        result = await get_image_vulnerabilities("my-repo")
        assert "error" in json.loads(result)


@pytest.mark.asyncio
class TestGetDatabaseInsights:
    async def test_rds(self, mock_database):
        result = await get_database_insights("my-db", "rds")
        ok(result)

    async def test_elasticache(self, mock_database):
        result = await get_database_insights("my-cache", "elasticache")
        ok(result)

    async def test_invalid_db_type(self):
        result = await get_database_insights("my-db", "mysql")
        assert "error" in json.loads(result)

    async def test_backend_error(self, mock_database):
        mock_database.get_database_insights.side_effect = Exception("RDS error")
        result = await get_database_insights("my-db")
        assert "error" in json.loads(result)


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

class TestDetectBackends:
    def test_defaults_to_loki_prometheus_tempo(self):
        with (
            patch.object(server_module.settings, "datadog_api_key", ""),
            patch.object(server_module.settings, "cloudwatch_log_group", ""),
            patch.object(server_module.settings, "loki_url", "http://loki:3100"),
            patch.object(server_module.settings, "prometheus_url", "http://prom:9090"),
            patch.object(server_module.settings, "tempo_url", "http://tempo:3200"),
        ):
            log, metrics, trace = server_module._detect_backends()
            assert isinstance(log, server_module.LokiClient)
            assert isinstance(metrics, server_module.PrometheusClient)
            assert isinstance(trace, server_module.TempoClient)

    def test_datadog_takes_priority(self):
        with patch.object(server_module.settings, "datadog_api_key", "dd-key-123"):
            log, metrics, _trace = server_module._detect_backends()
            assert isinstance(log, server_module.DatadogClient)
            assert isinstance(metrics, server_module.DatadogClient)

    def test_cloudwatch_when_no_datadog(self):
        with (
            patch.object(server_module.settings, "datadog_api_key", ""),
            patch.object(server_module.settings, "cloudwatch_log_group", "/aws/eks/app"),
        ):
            log, _metrics, _trace = server_module._detect_backends()
            assert isinstance(log, server_module.CloudWatchClient)

    def test_no_backends_returns_none(self):
        with (
            patch.object(server_module.settings, "datadog_api_key", ""),
            patch.object(server_module.settings, "cloudwatch_log_group", ""),
            patch.object(server_module.settings, "loki_url", ""),
            patch.object(server_module.settings, "prometheus_url", ""),
            patch.object(server_module.settings, "tempo_url", ""),
        ):
            log, metrics, trace = server_module._detect_backends()
            assert log is None
            assert metrics is None
            assert trace is None
