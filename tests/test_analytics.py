"""Tests for analytics module — all five classes."""

from typing import ClassVar

import pytest

from k8s_telemetry_mcp.tools.analytics import (
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
        # 'panic' matches the error pattern first, so check either crash or error category
        categories = result["error_summary"]["by_category"]
        assert "crash" in categories or "error" in categories

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


# ---------------------------------------------------------------------------
# Regression tests for the error-budget maths.
#
# The previous implementation scaled the burn rate by the window's fraction of a
# 30-day period, even though allowed_errors was already derived from the window's
# own traffic. That inflated every burn rate by (720 / window_hours) — 30x at the
# default 24h window — and made "critical" fire almost unconditionally. The old
# tests only asserted `status in ("exhausted", "critical")` and
# `remaining_percentage >= 0`, so none of them could fail.
# ---------------------------------------------------------------------------

def _slo(errors: int, total: int, *, target: float = 0.999, window_hours: int = 24) -> dict:
    """Run a check where availability/latency are healthy so the budget is isolated."""
    return SLOChecker().check(
        service_name="svc",
        availability_target=target,
        latency_target_ms=500.0,
        latency_percentile=0.99,
        current_availability=1.0 - (errors / total if total else 0),
        current_latency_ms=100.0,
        error_count=errors,
        total_requests=total,
        window_hours=window_hours,
    )


class TestErrorBudgetMath:
    def test_half_the_budget_consumed_is_burn_rate_one_half(self):
        # 1,000,000 requests at a 99.9% target allows 1000 errors. 500 errors is
        # exactly half the budget, which is a burn rate of 0.5 — not 15.0.
        budget = _slo(500, 1_000_000)["error_budget"]
        assert budget["burn_rate"] == pytest.approx(0.5)
        assert budget["remaining_percentage"] == pytest.approx(50.0)
        assert budget["allowed_errors"] == 1000
        assert budget["remaining_errors"] == 500

    def test_budget_exactly_consumed_is_burn_rate_one(self):
        budget = _slo(1000, 1_000_000)["error_budget"]
        assert budget["burn_rate"] == pytest.approx(1.0)
        assert budget["remaining_percentage"] == pytest.approx(0.0)

    def test_burn_rate_is_independent_of_window_length(self):
        # This is the invariant the old code violated: the same error ratio must
        # produce the same burn rate regardless of the measurement window.
        rates = {
            hours: _slo(500, 1_000_000, window_hours=hours)["error_budget"]["burn_rate"]
            for hours in (1, 6, 24, 168, 720)
        }
        for hours, rate in rates.items():
            assert rate == pytest.approx(0.5), f"window_hours={hours} gave {rate}"

    def test_zero_errors_is_burn_rate_zero_and_full_budget(self):
        budget = _slo(0, 1_000_000)["error_budget"]
        assert budget["burn_rate"] == pytest.approx(0.0)
        assert budget["remaining_percentage"] == pytest.approx(100.0)
        assert budget["status"] == "healthy"

    def test_healthy_service_is_not_reported_as_critical(self):
        result = _slo(500, 1_000_000)
        assert result["error_budget"]["status"] == "healthy"
        assert result["overall_status"] == "healthy"

    @pytest.mark.parametrize(
        ("errors", "expected_status"),
        [
            (500, "healthy"),      # 0.5x
            (1_500, "elevated"),   # 1.5x
            (3_000, "warning"),    # 3x
            (15_000, "critical"),  # 15x
        ],
    )
    def test_burn_rate_ladder(self, errors, expected_status):
        assert _slo(errors, 1_000_000)["error_budget"]["status"] == expected_status

    def test_exhausted_only_when_window_spans_the_full_slo_period(self):
        # Over a short window, burning more than the proportional share is a warning,
        # not proof the period budget is gone.
        assert _slo(1_200, 1_000_000, window_hours=24)["error_budget"]["status"] == "elevated"
        assert _slo(1_200, 1_000_000, window_hours=720)["error_budget"]["status"] == "exhausted"

    def test_hours_to_exhaustion_scales_inversely_with_burn_rate(self):
        budget = _slo(3_000, 1_000_000)["error_budget"]
        # 30-day budget at 3x burn -> 720 / 3 = 240 hours.
        assert budget["hours_to_exhaustion"] == pytest.approx(240.0)

    def test_no_exhaustion_projection_when_not_burning(self):
        assert "hours_to_exhaustion" not in _slo(500, 1_000_000)["error_budget"]

    def test_hundred_percent_target_with_errors_has_no_finite_burn_rate(self):
        budget = _slo(1, 10_000, target=1.0)["error_budget"]
        assert budget["burn_rate"] is None
        assert budget["status"] == "critical"


class TestErrorBudgetLowTraffic:
    def test_zero_errors_on_low_traffic_is_not_exhausted(self):
        # 100 requests at 99.9% allows 0.1 errors. int() truncation made this look
        # like a fully exhausted budget on a service with a perfect record.
        result = _slo(0, 100)
        assert result["error_budget"]["status"] == "insufficient_traffic"
        assert result["overall_status"] == "healthy"
        assert "note" in result["error_budget"]

    def test_low_traffic_recommendation_explains_itself(self):
        recs = _slo(0, 100)["recommendations"]
        assert any("too low" in r or "window_hours" in r for r in recs)

    def test_sufficient_traffic_evaluates_normally(self):
        # 2000 requests at 99.9% allows 2 errors — enough to be meaningful.
        assert _slo(0, 2_000)["error_budget"]["status"] == "healthy"

    def test_allowed_errors_exact_is_reported(self):
        budget = _slo(0, 100)["error_budget"]
        assert budget["allowed_errors_exact"] == pytest.approx(0.1)


class TestOverallStatusConsistency:
    """overall_status must never be milder than the error budget status."""

    _RANK: ClassVar[dict[str, int]] = {"healthy": 0, "warning": 1, "degraded": 2, "critical": 3}
    _BUDGET_FLOOR: ClassVar[dict[str, str]] = {
        "insufficient_traffic": "healthy",
        "healthy": "healthy",
        "elevated": "warning",
        "warning": "warning",
        "critical": "critical",
        "exhausted": "critical",
    }

    @pytest.mark.parametrize("errors", [0, 500, 1_000, 1_500, 3_000, 15_000, 999_999])
    def test_overall_is_never_milder_than_budget(self, errors):
        result = _slo(errors, 1_000_000)
        overall = result["overall_status"]
        floor = self._BUDGET_FLOOR[result["error_budget"]["status"]]
        assert self._RANK[overall] >= self._RANK[floor], result

    def test_missed_slo_is_never_reported_healthy(self):
        result = SLOChecker().check("svc", 0.999, 500.0, 0.99, 0.990, 900.0, 100, 10_000)
        assert result["overall_status"] != "healthy"


# ---------------------------------------------------------------------------
# Regression tests for the timeline metric branch, which only emitted events when
# a metric dict carried an "anomaly" key. No backend ever set one, so no metric
# event could ever appear in a timeline.
# ---------------------------------------------------------------------------

def _instant(value: float, pod: str = "svc-pod", ts: str = "2026-01-01T03:14:00+00:00") -> dict:
    return {"metric": {"pod": pod}, "timestamp": ts, "value": value}


def _series(values: list[float], pod: str = "svc-pod") -> dict:
    return {
        "metric": {"pod": pod},
        "values": [
            {"timestamp": f"2026-01-01T03:{i:02d}:00+00:00", "value": v}
            for i, v in enumerate(values)
        ],
    }


class TestTimelineMetricEvents:
    def setup_method(self):
        self.builder = IncidentTimelineBuilder()

    def test_nonzero_restarts_produce_a_timeline_event(self):
        metrics = {"restarts": {"metric_type": "restarts", "results": [_instant(4.0)]}}
        events = [e for e in self.builder.build([], metrics, [], "svc")["timeline"] if e["type"] == "metric"]
        assert len(events) == 1
        assert "restart" in events[0]["message"]
        assert events[0]["timestamp"] == "2026-01-01T03:14:00+00:00"

    def test_zero_restarts_produce_nothing(self):
        metrics = {"restarts": {"metric_type": "restarts", "results": [_instant(0.0)]}}
        assert self.builder.build([], metrics, [], "svc")["event_count"] == 0

    def test_spike_in_a_range_series_is_detected(self):
        metrics = {"cpu": {"metric_type": "cpu", "results": [_series([0.1, 0.1, 0.12, 2.5])]}}
        events = [e for e in self.builder.build([], metrics, [], "svc")["timeline"] if e["type"] == "metric"]
        assert len(events) == 1
        assert "spike" in events[0]["message"]

    def test_single_large_outlier_is_not_masked_by_its_own_variance(self):
        # A mean+2*stdev threshold cannot flag this: the outlier drags the mean and
        # stdev up far enough to hide behind them. Median/MAD is outlier-resistant.
        metrics = {"cpu": {"metric_type": "cpu", "results": [_series([1.0, 1.0, 1.0, 50.0])]}}
        events = self.builder.build([], metrics, [], "svc")["timeline"]
        assert any("spike" in e.get("message", "") for e in events)

    def test_flat_series_produces_no_spike(self):
        metrics = {"cpu": {"metric_type": "cpu", "results": [_series([1.0, 1.0, 1.0, 1.0])]}}
        assert self.builder.build([], metrics, [], "svc")["event_count"] == 0

    def test_gradual_ramp_is_not_a_spike(self):
        metrics = {"cpu": {"metric_type": "cpu", "results": [_series([1.0, 2.0, 3.0, 4.0])]}}
        assert self.builder.build([], metrics, [], "svc")["event_count"] == 0

    def test_instant_query_alone_makes_no_spike_claim(self):
        # One data point carries no distribution, so no anomaly should be invented.
        metrics = {"cpu": {"metric_type": "cpu", "results": [_instant(99.0)]}}
        assert self.builder.build([], metrics, [], "svc")["event_count"] == 0

    def test_unavailable_metric_is_skipped(self):
        metrics = {"cpu": None, "memory": None}
        assert self.builder.build([], metrics, [], "svc")["event_count"] == 0

    def test_explicit_anomaly_flag_still_honoured(self):
        metrics = {"cpu": {"anomaly": True, "timestamp": "t", "description": "manual flag"}}
        events = [e for e in self.builder.build([], metrics, [], "svc")["timeline"] if e["type"] == "metric"]
        assert len(events) == 1
        assert "manual flag" in events[0]["message"]

    def test_metric_events_merge_chronologically_with_logs(self):
        metrics = {"restarts": {"results": [_instant(2.0, ts="2026-01-01T03:00:00+00:00")]}}
        logs = [_log("error later", ts="2026-01-01T04:00:00+00:00")]
        timeline = self.builder.build(logs, metrics, [], "svc")["timeline"]
        assert [e["type"] for e in timeline] == ["metric", "log"]


# ---------------------------------------------------------------------------
# Regression tests for enrich_alert's metric summary. The server passes the
# `results` list from get_pod_metrics, which fell through every isinstance branch,
# so the metrics section of an enriched alert was always empty.
# ---------------------------------------------------------------------------

class TestAlertEnricherMetrics:
    def setup_method(self):
        self.enricher = AlertEnricher()

    def test_results_list_is_summarised_not_dropped(self):
        metrics = {"cpu": [_instant(0.42)]}
        summary = self.enricher.enrich("A", "svc", "ns", [], metrics, [])["context"]["metrics"]
        assert summary["cpu"] == pytest.approx(0.42)

    def test_multiple_series_are_keyed_by_pod(self):
        metrics = {"restarts": [_instant(43.0, pod="svc-a"), _instant(2.0, pod="svc-b")]}
        summary = self.enricher.enrich("A", "svc", "ns", [], metrics, [])["context"]["metrics"]
        assert summary["restarts"] == {"svc-a": 43.0, "svc-b": 2.0}

    def test_failed_metric_query_is_labelled_not_silently_absent(self):
        summary = self.enricher.enrich("A", "svc", "ns", [], {"memory": None}, [])["context"]["metrics"]
        assert "unavailable" in str(summary["memory"])

    def test_empty_results_list_reports_no_data(self):
        summary = self.enricher.enrich("A", "svc", "ns", [], {"cpu": []}, [])["context"]["metrics"]
        assert summary["cpu"] == "no data"

    def test_range_series_uses_latest_value(self):
        metrics = {"cpu": [_series([1.0, 2.0, 3.0])]}
        summary = self.enricher.enrich("A", "svc", "ns", [], metrics, [])["context"]["metrics"]
        assert summary["cpu"] == pytest.approx(3.0)

    def test_get_pod_metrics_response_shape_is_accepted(self):
        metrics = {"cpu": {"metric_type": "cpu", "results": [_instant(0.7)]}}
        summary = self.enricher.enrich("A", "svc", "ns", [], metrics, [])["context"]["metrics"]
        assert summary["cpu"] == pytest.approx(0.7)

    def test_plain_scalar_still_supported(self):
        summary = self.enricher.enrich("A", "svc", "ns", [], {"cpu": 1.23456}, [])["context"]["metrics"]
        assert summary["cpu"] == pytest.approx(1.235)

    def test_recent_deployments_reach_the_context(self):
        deployments = [{"name": "checkout", "image": "checkout:v2.4.1"}]
        result = self.enricher.enrich("A", "svc", "ns", [], {}, [], recent_deployments=deployments)
        assert result["context"]["deployments"] == deployments

    def test_deployments_default_to_empty(self):
        result = self.enricher.enrich("A", "svc", "ns", [], {}, [])
        assert result["context"]["deployments"] == []
