"""Tests for analytics module — all five classes."""

import pytest

from src.tools.analytics import (
    AlertEnricher,
    CostAnalyzer,
    IncidentTimelineBuilder,
    LogAnalyzer,
    SLOChecker,
)


def _log(msg: str, ts: str = "2024-01-01T03:00:00Z") -> dict:
    return {"timestamp": ts, "message": msg, "labels": {"pod": "svc-pod"}}


# ---------------------------------------------------------------------------
# LogAnalyzer
# ---------------------------------------------------------------------------

class TestLogAnalyzer:
    def setup_method(self):
        self.analyzer = LogAnalyzer()

    def test_empty_logs(self):
        result = self.analyzer.analyze([])
        assert result["error_count"] == 0

    def test_counts_total_logs(self):
        logs = [_log("info ok"), _log("info ok2")]
        result = self.analyzer.analyze(logs)
        assert result["total_logs"] == 2

    def test_detects_error_category(self):
        logs = [_log("ERROR: something failed")]
        result = self.analyzer.analyze(logs)
        assert result["error_summary"]["by_category"].get("error", 0) >= 1

    def test_detects_timeout(self):
        logs = [_log("connection timed out")]
        result = self.analyzer.analyze(logs)
        assert "timeout" in result["error_summary"]["by_category"]

    def test_detects_oom(self):
        logs = [_log("out of memory: kill process")]
        result = self.analyzer.analyze(logs)
        assert "memory" in result["error_summary"]["by_category"]

    def test_detects_connection_error(self):
        logs = [_log("connection refused")]
        result = self.analyzer.analyze(logs)
        assert "connection" in result["error_summary"]["by_category"]

    def test_detects_auth_error(self):
        logs = [_log("permission denied")]
        result = self.analyzer.analyze(logs)
        assert "auth" in result["error_summary"]["by_category"]

    def test_detects_crash(self):
        logs = [_log("panic: runtime error")]
        result = self.analyzer.analyze(logs)
        assert "crash" in result["error_summary"]["by_category"]

    def test_detects_rate_limit(self):
        logs = [_log("rate limit exceeded")]
        result = self.analyzer.analyze(logs)
        assert "rate_limit" in result["error_summary"]["by_category"]

    def test_time_range_populated(self):
        logs = [_log("ok", "2024-01-01T01:00:00Z"), _log("ok", "2024-01-01T02:00:00Z")]
        result = self.analyzer.analyze(logs)
        assert result["time_range"]["start"] == "2024-01-01T01:00:00Z"
        assert result["time_range"]["end"] == "2024-01-01T02:00:00Z"

    def test_recommendations_not_empty(self):
        logs = [_log("ERROR: crash detected")]
        result = self.analyzer.analyze(logs)
        assert len(result["recommendations"]) > 0

    def test_no_errors_recommendation(self):
        logs = [_log("all systems nominal")]
        result = self.analyzer.analyze(logs)
        assert any("No critical" in r for r in result["recommendations"])

    def test_repetition_ratio(self):
        logs = [_log("same message")] * 10
        result = self.analyzer.analyze(logs)
        assert result["patterns"]["repetition_ratio"] > 0

    def test_upsell_key_present(self):
        """enrich_alert and build_incident_timeline must include _automate upsell."""
        # This is tested via AlertEnricher and IncidentTimelineBuilder below
        pass


# ---------------------------------------------------------------------------
# IncidentTimelineBuilder
# ---------------------------------------------------------------------------

class TestIncidentTimelineBuilder:
    def setup_method(self):
        self.builder = IncidentTimelineBuilder()

    def test_empty_inputs(self):
        result = self.builder.build([], {}, [], "svc")
        assert result["event_count"] == 0
        assert result["service"] == "svc"

    def test_error_logs_included(self):
        logs = [_log("ERROR: pod crashed", "2024-01-01T03:00:00Z")]
        result = self.builder.build(logs, {}, [], "svc")
        assert result["event_count"] >= 1
        assert result["timeline"][0]["type"] == "log"
        assert result["timeline"][0]["severity"] == "error"

    def test_info_logs_excluded(self):
        logs = [_log("info: all good")]
        result = self.builder.build(logs, {}, [], "svc")
        assert result["event_count"] == 0

    def test_warning_logs_included(self):
        logs = [_log("warning: high memory")]
        result = self.builder.build(logs, {}, [], "svc")
        assert result["event_count"] == 1
        assert result["timeline"][0]["severity"] == "warning"

    def test_trace_errors_included(self):
        traces = [{"trace_id": "abc", "service": "svc", "operation": "GET /", "duration": "1s", "error": True, "timestamp": "2024-01-01T03:00:00Z"}]
        result = self.builder.build([], {}, traces, "svc")
        assert result["event_count"] == 1
        assert result["timeline"][0]["type"] == "trace"

    def test_timeline_sorted_by_timestamp(self):
        logs = [
            _log("ERROR: b", "2024-01-01T03:02:00Z"),
            _log("ERROR: a", "2024-01-01T03:01:00Z"),
        ]
        result = self.builder.build(logs, {}, [], "svc")
        assert result["timeline"][0]["timestamp"] < result["timeline"][1]["timestamp"]

    def test_summary_counts(self):
        logs = [_log("ERROR: crash"), _log("warning: slow")]
        result = self.builder.build(logs, {}, [], "svc")
        assert result["summary"]["errors"] == 1
        assert result["summary"]["warnings"] == 1

    def test_upsell_key_present(self):
        result = self.builder.build([], {}, [], "svc")
        assert "_automate" in result
        assert "kubeopsai.net" in result["_automate"]


# ---------------------------------------------------------------------------
# AlertEnricher
# ---------------------------------------------------------------------------

class TestAlertEnricher:
    def setup_method(self):
        self.enricher = AlertEnricher()

    def test_basic_structure(self):
        result = self.enricher.enrich("TestAlert", "svc", "default", [], {}, [])
        assert result["alert"] == "TestAlert"
        assert result["service"] == "svc"
        assert result["namespace"] == "default"
        assert "context" in result
        assert "recommendations" in result

    def test_upsell_key_present(self):
        result = self.enricher.enrich("TestAlert", "svc", "default", [], {}, [])
        assert "_automate" in result
        assert "kubeopsai.net" in result["_automate"]

    def test_log_count_in_context(self):
        logs = [_log("error"), _log("error2")]
        result = self.enricher.enrich("A", "svc", "ns", logs, {}, [])
        assert result["context"]["logs"]["count"] == 2

    def test_trace_error_rate(self):
        traces = [
            {"error": True, "status_code": 500},
            {"error": False, "status_code": 200},
        ]
        result = self.enricher.enrich("A", "svc", "ns", [], {}, traces)
        assert result["context"]["traces"]["error_rate"] == 50.0

    def test_no_traces_status(self):
        result = self.enricher.enrich("A", "svc", "ns", [], {}, [])
        assert result["context"]["traces"]["status"] == "No traces available"

    def test_suggested_queries_not_empty(self):
        result = self.enricher.enrich("A", "svc", "ns", [], {}, [])
        assert len(result["suggested_queries"]) >= 2

    def test_memory_error_adds_query(self):
        logs = [_log("out of memory")]
        result = self.enricher.enrich("A", "svc", "ns", logs, {}, [])
        assert any("memory" in q for q in result["suggested_queries"])

    def test_timeout_error_adds_query(self):
        logs = [_log("connection timed out")]
        result = self.enricher.enrich("A", "svc", "ns", logs, {}, [])
        assert any("duration" in q or "quantile" in q for q in result["suggested_queries"])


# ---------------------------------------------------------------------------
# CostAnalyzer
# ---------------------------------------------------------------------------

class TestCostAnalyzer:
    def setup_method(self):
        self.analyzer = CostAnalyzer()

    def test_empty_inputs(self):
        result = self.analyzer.analyze({}, {})
        assert result["total_hourly_cost"] == 0.0
        assert result["by_resource"] == {}

    def test_single_namespace(self):
        result = self.analyzer.analyze({"default": 1.0}, {"default": 1024 ** 3})
        assert "default" in result["by_resource"]
        ns = result["by_resource"]["default"]
        assert ns["cpu_cores"] == 1.0
        assert ns["memory_gb"] == 1.0
        assert ns["cpu_cost_hourly"] == pytest.approx(0.05)
        assert ns["memory_cost_hourly"] == pytest.approx(0.01)

    def test_projected_monthly(self):
        result = self.analyzer.analyze({"ns": 1.0}, {})
        ns = result["by_resource"]["ns"]
        assert ns["projected_monthly"] == pytest.approx(ns["total_cost_hourly"] * 24 * 30, rel=0.01)

    def test_sorted_by_cost_descending(self):
        result = self.analyzer.analyze({"cheap": 0.1, "expensive": 10.0}, {})
        keys = list(result["by_resource"].keys())
        assert keys[0] == "expensive"

    def test_top_consumers(self):
        cpu = {f"ns{i}": float(i) for i in range(6)}
        result = self.analyzer.analyze(cpu, {})
        assert len(result["top_consumers"]) == 5

    def test_low_utilization_suggestion(self):
        result = self.analyzer.analyze({"tiny": 0.05}, {"tiny": 100 * 1024 * 1024})
        assert any("consolidation" in s for s in result["optimization_suggestions"])

    def test_high_cpu_suggestion(self):
        result = self.analyzer.analyze({"heavy": 5.0}, {})
        assert any("CPU" in s or "cpu" in s.lower() for s in result["optimization_suggestions"])


# ---------------------------------------------------------------------------
# SLOChecker
# ---------------------------------------------------------------------------

class TestSLOChecker:
    def setup_method(self):
        self.checker = SLOChecker()

    def test_no_data_status(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.0, 0.0, 0, 0)
        assert result["overall_status"] == "no_data"
        assert result["availability"]["current"] is None

    def test_healthy_status(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.9995, 200.0, 5, 10000)
        assert result["overall_status"] == "healthy"
        assert result["availability"]["met"] is True
        assert result["latency"]["met"] is True

    def test_degraded_availability(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.990, 200.0, 100, 10000)
        assert result["availability"]["met"] is False
        assert result["overall_status"] in ("degraded", "critical", "warning")

    def test_degraded_latency(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.9995, 800.0, 5, 10000)
        assert result["latency"]["met"] is False

    def test_error_budget_exhausted(self):
        # 100% errors on 10000 requests
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.0, 0.0, 10000, 10000)
        assert result["error_budget"]["status"] in ("exhausted", "critical")

    def test_error_budget_remaining(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.9995, 200.0, 5, 10000)
        assert result["error_budget"]["remaining_percentage"] >= 0

    def test_recommendations_not_empty(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.990, 800.0, 100, 10000)
        assert len(result["recommendations"]) > 0

    def test_hours_to_exhaustion_present_when_burning(self):
        # High burn rate scenario
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.95, 200.0, 500, 10000, window_hours=1)
        if result["error_budget"]["burn_rate"] > 1 and result["error_budget"]["remaining_percentage"] > 0:
            assert "hours_to_exhaustion" in result["error_budget"]

    def test_service_name_in_result(self):
        result = self.checker.check("my-service", 0.999, 500.0, 0.99, 0.9995, 200.0, 5, 10000)
        assert result["service"] == "my-service"

    def test_window_hours_in_result(self):
        result = self.checker.check("svc", 0.999, 500.0, 0.99, 0.9995, 200.0, 5, 10000, window_hours=48)
        assert result["window_hours"] == 48
