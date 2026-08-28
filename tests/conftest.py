"""Shared fixtures for MCP server tests."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.server as server_module
from src.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_log_entry(message: str, ts: str | None = None) -> dict:
    return {"timestamp": ts or "2024-01-01T03:00:00Z", "message": message, "labels": {"pod": "test-pod"}}


def make_trace(service: str = "svc", error: bool = False) -> dict:
    return {"trace_id": "abc123", "service": service, "operation": "GET /", "duration": "100ms", "error": error}


# ---------------------------------------------------------------------------
# Mock clients
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_loki():
    client = AsyncMock()
    client.query_logs = AsyncMock(return_value=[make_log_entry("info message")])
    client.query_raw = AsyncMock(return_value=[make_log_entry("raw result")])
    return client


@pytest.fixture
def mock_prometheus():
    client = AsyncMock()
    client.query_instant = AsyncMock(return_value=[{"metric": {"namespace": "default"}, "value": 0.5}])
    client.query_range = AsyncMock(return_value=[{"metric": {}, "values": [[1700000000, "0.5"]]}])
    client.get_pod_metrics = AsyncMock(return_value={"results": [{"value": 0.5}]})
    client.get_cluster_health = AsyncMock(return_value={"nodes": 3, "pods_running": 10})
    # Mark as PrometheusClient instance
    client.__class__ = server_module.PrometheusClient
    return client


@pytest.fixture
def mock_tempo():
    client = AsyncMock()
    client.get_trace = AsyncMock(return_value={"trace_id": "abc123def456abcd", "spans": []})
    client.search_traces = AsyncMock(return_value=[make_trace()])
    return client


@pytest.fixture
def mock_k8s():
    client = AsyncMock()
    client.get_events = AsyncMock(return_value={"events": [], "count": 0})
    client.get_scaling_history = AsyncMock(return_value={"hpa": [], "deployments": []})
    client.get_node_pressure = AsyncMock(return_value={"nodes": []})
    client.get_recent_deployments = AsyncMock(return_value={"deployments": []})
    return client


@pytest.fixture
def mock_alertmanager():
    client = AsyncMock()
    client.get_alert_history = AsyncMock(return_value={"alerts": [], "silences": []})
    return client


@pytest.fixture
def mock_cloudtrail():
    client = AsyncMock()
    client.query_events = AsyncMock(return_value={"events": []})
    client.get_resource_history = AsyncMock(return_value={"events": []})
    return client


@pytest.fixture
def mock_awsconfig():
    client = AsyncMock()
    client.get_resource_compliance = AsyncMock(return_value={"rules": []})
    return client


@pytest.fixture
def mock_ecr():
    client = AsyncMock()
    client.get_image_vulnerabilities = AsyncMock(return_value={"findings": [], "total": 0})
    return client


@pytest.fixture
def mock_database():
    client = AsyncMock()
    client.get_database_insights = AsyncMock(return_value={"metrics": {}})
    return client


# ---------------------------------------------------------------------------
# Patch all server globals
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_server_clients(
    mock_loki, mock_prometheus, mock_tempo, mock_k8s,
    mock_alertmanager, mock_cloudtrail, mock_awsconfig, mock_ecr, mock_database,
):
    with (
        patch.object(server_module, "_log_client", mock_loki),
        patch.object(server_module, "_metrics_client", mock_prometheus),
        patch.object(server_module, "_trace_client", mock_tempo),
        patch.object(server_module, "_k8s_client", mock_k8s),
        patch.object(server_module, "_alertmanager_client", mock_alertmanager),
        patch.object(server_module, "_cloudtrail_client", mock_cloudtrail),
        patch.object(server_module, "_awsconfig_client", mock_awsconfig),
        patch.object(server_module, "_ecr_client", mock_ecr),
        patch.object(server_module, "_database_client", mock_database),
    ):
        yield
