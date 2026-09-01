"""Tests for the CloudWatch client's error/empty-result distinction.

Before this, a real ClientError (permission denied, throttling) on a metric call was
caught and silently converted into the same shape as "no datapoints in this range" —
`results: []` or a bare `None`. That is exactly the "no data returned" vs "nothing
happened" conflation this package already fixed for CloudTrail and AWS Config. These
tests pin that the fix now applies to CloudWatch metrics too.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.tools.cloudwatch import CloudWatchClient


def _client_error(code: str = "AccessDeniedException", message: str = "denied") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "GetMetricStatistics")


@pytest.fixture
def client(monkeypatch):
    c = CloudWatchClient(region="us-east-1")
    fake = MagicMock()
    monkeypatch.setattr(c, "_metrics_client", lambda: fake)
    return c, fake


class TestQueryMetrics:
    async def test_a_real_api_error_is_reported_not_hidden(self, client):
        c, fake = client
        fake.get_metric_statistics.side_effect = _client_error("ThrottlingException", "rate exceeded")

        result = await c.query_metrics(pod_name="checkout-api", namespace="prod", metric_type="cpu")

        assert result["results"] == []
        assert "error" in result
        assert "ThrottlingException" in result["error"]
        assert "rate exceeded" in result["error"]

    async def test_a_genuinely_empty_result_carries_no_error(self, client):
        c, fake = client
        fake.get_metric_statistics.return_value = {"Datapoints": []}

        result = await c.query_metrics(pod_name="checkout-api", namespace="prod", metric_type="cpu")

        assert result["results"] == []
        assert "error" not in result

    async def test_a_successful_call_with_data_is_unaffected(self, client):
        c, fake = client
        now = datetime.now(UTC)
        fake.get_metric_statistics.return_value = {
            "Datapoints": [{"Timestamp": now, "Average": 42.0}]
        }

        result = await c.query_metrics(pod_name="checkout-api", namespace="prod", metric_type="cpu")

        assert result["results"][0]["value"] == 42.0
        assert "error" not in result


class TestGetClusterHealth:
    async def test_a_failed_metric_is_recorded_by_name(self, client):
        c, fake = client
        fake.get_metric_statistics.side_effect = _client_error("AccessDeniedException", "no permission")

        result = await c.get_cluster_health()

        assert result["node_count"] is None
        assert "errors" in result
        # All four metrics failed the same way; each is named so the caller can tell
        # which specific CloudWatch permission is missing.
        assert set(result["errors"]) == {
            "cluster_node_count", "cluster_pod_count", "node_cpu_utilization", "node_memory_utilization",
        }
        assert all("AccessDeniedException" in msg for msg in result["errors"].values())

    async def test_no_errors_key_when_everything_succeeds(self, client):
        c, fake = client
        now = datetime.now(UTC)
        fake.get_metric_statistics.return_value = {"Datapoints": [{"Timestamp": now, "Average": 3.0}]}

        result = await c.get_cluster_health()

        assert "errors" not in result
        assert result["node_count"] == 3

    async def test_a_partial_failure_only_names_the_metric_that_failed(self, client):
        c, fake = client
        now = datetime.now(UTC)

        def side_effect(**kwargs):
            if kwargs.get("MetricName") == "cluster_node_count":
                raise _client_error("ThrottlingException", "slow down")
            return {"Datapoints": [{"Timestamp": now, "Average": 1.0}]}

        fake.get_metric_statistics.side_effect = side_effect

        result = await c.get_cluster_health()

        assert list(result["errors"]) == ["cluster_node_count"]
        assert result["node_count"] is None
        assert result["pod_count"] == 1
