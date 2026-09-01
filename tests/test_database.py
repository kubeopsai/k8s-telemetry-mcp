"""Tests for the RDS/ElastiCache client's error/empty-result distinction.

The CloudWatch-backed fallback paths (RDS Performance-Insights-disabled fallback, and
ElastiCache metrics) previously caught ClientError per metric and discarded it
silently, so a permission or throttling failure on one metric looked identical to that
metric genuinely having no datapoints in the window. Pinned here alongside the same
fix in test_cloudwatch.py.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.tools.database import DatabaseInsightsClient


def _client_error(code: str = "AccessDeniedException", message: str = "denied") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "GetMetricStatistics")


@pytest.fixture
def client(monkeypatch):
    c = DatabaseInsightsClient(region="us-east-1")
    fake_cw = MagicMock()
    monkeypatch.setattr(c, "_cw_client", lambda: fake_cw)
    return c, fake_cw


class TestRdsCloudWatchFallback:
    async def test_a_failed_metric_call_is_reported_by_name(self, client):
        c, fake_cw = client
        fake_cw.get_metric_statistics.side_effect = _client_error("ThrottlingException", "rate exceeded")

        result = await c._rds_cloudwatch_fallback(
            "my-db", "postgres", datetime.now(UTC), datetime.now(UTC), timeframe_minutes=60,
        )

        assert result["metrics"] == {}
        assert "errors" in result
        assert "ThrottlingException" in result["errors"]["DatabaseConnections"]

    async def test_no_errors_key_when_everything_succeeds(self, client):
        c, fake_cw = client
        now = datetime.now(UTC)
        fake_cw.get_metric_statistics.return_value = {
            "Datapoints": [{"Timestamp": now, "Average": 1.0, "Maximum": 2.0}]
        }

        result = await c._rds_cloudwatch_fallback(
            "my-db", "postgres", datetime.now(UTC), datetime.now(UTC), timeframe_minutes=60,
        )

        assert "errors" not in result
        assert result["metrics"]["DatabaseConnections"]["maximum"] == 2.0

    async def test_a_partial_failure_only_names_the_failed_metric(self, client):
        c, fake_cw = client
        now = datetime.now(UTC)

        def side_effect(**kwargs):
            if kwargs.get("MetricName") == "ReadLatency":
                raise _client_error("AccessDeniedException", "no permission")
            return {"Datapoints": [{"Timestamp": now, "Average": 1.0, "Maximum": 2.0}]}

        fake_cw.get_metric_statistics.side_effect = side_effect

        result = await c._rds_cloudwatch_fallback(
            "my-db", "postgres", datetime.now(UTC), datetime.now(UTC), timeframe_minutes=60,
        )

        assert list(result["errors"]) == ["ReadLatency"]
        assert "ReadLatency" not in result["metrics"]
        assert "WriteLatency" in result["metrics"]


class TestElasticacheMetrics:
    async def test_a_failed_metric_call_is_reported_by_name(self, client):
        c, fake_cw = client
        fake_cw.get_metric_statistics.side_effect = _client_error("ThrottlingException", "rate exceeded")

        result = await c._elasticache_metrics("my-cache", datetime.now(UTC), datetime.now(UTC))

        assert result["metrics"] == {}
        assert "errors" in result
        assert "ThrottlingException" in result["errors"]["CurrConnections"]

    async def test_no_errors_key_when_everything_succeeds(self, client):
        c, fake_cw = client
        now = datetime.now(UTC)
        fake_cw.get_metric_statistics.return_value = {
            "Datapoints": [{"Timestamp": now, "Average": 1.0, "Sum": 5.0}]
        }

        result = await c._elasticache_metrics("my-cache", datetime.now(UTC), datetime.now(UTC))

        assert "errors" not in result
        assert result["metrics"]["CurrConnections"]["sum"] == 5.0
