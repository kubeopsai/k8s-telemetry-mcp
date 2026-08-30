"""Analytics module for advanced observability insights."""

import re
from collections import Counter
from typing import Any, ClassVar


class LogAnalyzer:
    """Analyze logs for patterns, anomalies, and insights."""

    # Common error patterns to detect
    ERROR_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"(?i)error|exception|failed|failure", "error"),
        (r"(?i)timeout|timed?\s*out", "timeout"),
        (r"(?i)connection\s*(refused|reset|closed)", "connection"),
        (r"(?i)out\s*of\s*memory|oom|memory\s*limit", "memory"),
        (r"(?i)permission\s*denied|unauthorized|forbidden", "auth"),
        (r"(?i)not\s*found|404|missing", "not_found"),
        (r"(?i)rate\s*limit|throttl", "rate_limit"),
        (r"(?i)crash|panic|segfault", "crash"),
    ]

    def analyze(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze logs and return structured insights."""
        if not logs:
            return {"summary": "No logs to analyze", "error_count": 0}

        messages = [log.get("message", "") for log in logs]
        timestamps = [log.get("timestamp", "") for log in logs]

        error_summary = self._categorize_errors(messages)
        time_analysis = self._analyze_time_distribution(timestamps, messages)
        patterns = self._detect_patterns(messages)
        
        return {
            "total_logs": len(logs),
            "time_range": {
                "start": min(timestamps) if timestamps else None,
                "end": max(timestamps) if timestamps else None,
            },
            "error_summary": error_summary,
            "time_analysis": time_analysis,
            "patterns": patterns,
            "recommendations": self._generate_recommendations(error_summary, patterns),
        }

    def _categorize_errors(self, messages: list[str]) -> dict[str, Any]:
        """Categorize errors by type."""
        categories: dict[str, list[str]] = {}
        
        for msg in messages:
            for pattern, category in self.ERROR_PATTERNS:
                if re.search(pattern, msg):
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(msg[:200])
                    break

        return {
            "by_category": {k: len(v) for k, v in categories.items()},
            "total_errors": sum(len(v) for v in categories.values()),
            "sample_errors": {k: v[:3] for k, v in categories.items()},
        }

    def _analyze_time_distribution(self, timestamps: list[str], messages: list[str]) -> dict[str, Any]:
        """Analyze error distribution over time."""
        if not timestamps:
            return {}

        error_times = []
        for ts, msg in zip(timestamps, messages):
            for pattern, _ in self.ERROR_PATTERNS:
                if re.search(pattern, msg):
                    error_times.append(ts)
                    break

        if not error_times:
            return {"error_rate": "No errors detected"}

        return {
            "first_error": min(error_times),
            "last_error": max(error_times),
            "error_count": len(error_times),
            "error_percentage": round(len(error_times) / len(timestamps) * 100, 1),
        }

    def _detect_patterns(self, messages: list[str]) -> dict[str, Any]:
        """Detect recurring patterns in logs."""
        # Extract common prefixes/patterns
        word_freq = Counter()
        for msg in messages:
            words = msg.split()[:5]  # First 5 words
            if words:
                word_freq[" ".join(words)] += 1

        recurring = {k: v for k, v in word_freq.most_common(10) if v > 1}
        
        return {
            "recurring_patterns": recurring,
            "unique_messages": len(set(messages)),
            "repetition_ratio": round(1 - len(set(messages)) / max(len(messages), 1), 2),
        }

    def _generate_recommendations(self, errors: dict, patterns: dict) -> list[str]:
        """Generate actionable recommendations."""
        recs = []
        
        by_cat = errors.get("by_category", {})
        
        if by_cat.get("timeout", 0) > 0:
            recs.append("Investigate timeout errors - check downstream service health and network latency")
        if by_cat.get("memory", 0) > 0:
            recs.append("Memory issues detected - review resource limits and check for memory leaks")
        if by_cat.get("connection", 0) > 0:
            recs.append("Connection errors found - verify network policies and service endpoints")
        if by_cat.get("auth", 0) > 0:
            recs.append("Authentication failures - check credentials and RBAC permissions")
        if by_cat.get("rate_limit", 0) > 0:
            recs.append("Rate limiting detected - consider scaling or implementing backoff")
        if by_cat.get("crash", 0) > 0:
            recs.append("CRITICAL: Crash/panic detected - immediate investigation required")
        
        if patterns.get("repetition_ratio", 0) > 0.5:
            recs.append("High log repetition detected - consider deduplication or fixing root cause")
        
        if not recs:
            recs.append("No critical issues detected in analyzed logs")
        
        return recs


class IncidentTimelineBuilder:
    """Build incident timelines from observability data."""

    def build(
        self,
        logs: list[dict],
        metrics: dict[str, Any],
        traces: list[dict],
        service_name: str,
    ) -> dict[str, Any]:
        """Build a chronological incident timeline."""
        events = []

        # Extract log events
        for log in logs:
            ts = log.get("timestamp", "")
            msg = log.get("message", "")
            severity = self._classify_severity(msg)
            if severity in ("error", "warning"):
                events.append({
                    "timestamp": ts,
                    "type": "log",
                    "severity": severity,
                    "source": log.get("labels", {}).get("pod", "unknown"),
                    "message": msg[:200],
                })

        # Extract metric anomalies
        events.extend(self._extract_metric_events(metrics))

        # Extract trace errors
        for trace in traces:
            if trace.get("error") or trace.get("status_code", 200) >= 400:
                events.append({
                    "timestamp": trace.get("timestamp", ""),
                    "type": "trace",
                    "severity": "error",
                    "source": trace.get("service", service_name),
                    "message": f"Failed request: {trace.get('operation', 'unknown')} - {trace.get('duration', 'N/A')}",
                })

        # Sort by timestamp
        events.sort(key=lambda x: x.get("timestamp", ""))

        return {
            "service": service_name,
            "event_count": len(events),
            "timeline": events,
            "summary": self._summarize_timeline(events),
            "_automate": "Want this at 3 AM without anyone at a keyboard? → https://kubeopsai.net",
        }

    @staticmethod
    def _metric_points(data: Any) -> list[dict[str, Any]]:
        """Flatten a metrics payload into a list of {timestamp, value, series} points.

        Accepts the shape returned by ``get_pod_metrics``
        (``{"metric_type": ..., "results": [...]}``), a bare results list, or a
        range-query result where each series carries a ``values`` list.
        """
        if isinstance(data, dict):
            results = data.get("results", [])
        elif isinstance(data, list):
            results = data
        else:
            return []

        points: list[dict[str, Any]] = []
        for series in results:
            if not isinstance(series, dict):
                continue
            labels = series.get("metric", {}) if isinstance(series.get("metric"), dict) else {}
            series_name = labels.get("pod") or labels.get("namespace") or ""
            if "values" in series and isinstance(series["values"], list):
                for point in series["values"]:
                    if isinstance(point, dict) and isinstance(point.get("value"), (int, float)):
                        points.append({
                            "timestamp": point.get("timestamp", ""),
                            "value": float(point["value"]),
                            "series": series_name,
                        })
            elif isinstance(series.get("value"), (int, float)):
                points.append({
                    "timestamp": series.get("timestamp", ""),
                    "value": float(series["value"]),
                    "series": series_name,
                })
        return points

    def _extract_metric_events(self, metrics: Any) -> list[dict[str, Any]]:
        """Derive timeline events from metric data.

        Two detections, both grounded in real values rather than an ``anomaly`` flag
        that no backend ever set:
          * ``restarts`` — any non-zero count is timeline-worthy on its own.
          * everything else — points more than two standard deviations above the
            series mean, which needs at least 3 samples (i.e. a range query). Instant
            queries yield a single point, so no anomaly is claimed for them.
        """
        if not isinstance(metrics, dict):
            return []

        events: list[dict[str, Any]] = []
        for metric_name, data in metrics.items():
            # Preserve support for a pre-computed anomaly marker.
            if isinstance(data, dict) and data.get("anomaly"):
                events.append({
                    "timestamp": data.get("timestamp", ""),
                    "type": "metric",
                    "severity": "warning",
                    "source": metric_name,
                    "message": f"{metric_name}: {data.get('description', 'anomaly detected')}",
                })
                continue

            points = self._metric_points(data)
            if not points:
                continue

            if "restart" in str(metric_name).lower():
                for point in points:
                    if point["value"] > 0:
                        target = f" ({point['series']})" if point["series"] else ""
                        events.append({
                            "timestamp": point["timestamp"],
                            "type": "metric",
                            "severity": "warning",
                            "source": metric_name,
                            "message": f"{int(point['value'])} container restart(s) observed{target}",
                        })
                continue

            if len(points) < 3:
                continue

            values = [p["value"] for p in points]
            baseline = self._median(values)
            # Median absolute deviation rather than standard deviation: a single large
            # spike inflates the mean and stdev enough to hide itself behind a 2σ
            # threshold, which is exactly the case this detection exists for.
            mad = self._median([abs(v - baseline) for v in values])
            robust_sigma = 1.4826 * mad  # scales MAD to be comparable to stdev

            if robust_sigma > 0:
                threshold = baseline + 3 * robust_sigma
            else:
                # Flat baseline — require a clear multiplicative jump instead.
                threshold = baseline * 1.5 if baseline > 0 else 0.0

            for point in points:
                value = point["value"]
                # The relative floor keeps small absolute wobble on a near-zero
                # baseline from being reported as a spike.
                if value > threshold and value > baseline * 1.5 and value > 0:
                    target = f" ({point['series']})" if point["series"] else ""
                    events.append({
                        "timestamp": point["timestamp"],
                        "type": "metric",
                        "severity": "warning",
                        "source": metric_name,
                        "message": (
                            f"{metric_name} spike{target}: {value:.4g} "
                            f"vs window median {baseline:.4g}"
                        ),
                    })
        return events

    @staticmethod
    def _median(values: list[float]) -> float:
        """Median of a non-empty list of floats."""
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def _classify_severity(self, message: str) -> str:
        """Classify log message severity."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["error", "exception", "fatal", "crash", "panic"]):
            return "error"
        if any(w in msg_lower for w in ["warn", "warning", "timeout"]):
            return "warning"
        return "info"

    def _summarize_timeline(self, events: list[dict]) -> dict[str, Any]:
        """Generate timeline summary."""
        if not events:
            return {"status": "No significant events found"}

        error_count = sum(1 for e in events if e.get("severity") == "error")
        warning_count = sum(1 for e in events if e.get("severity") == "warning")
        sources = Counter(e.get("source") for e in events)
        
        return {
            "total_events": len(events),
            "errors": error_count,
            "warnings": warning_count,
            "affected_components": dict(sources.most_common(5)),
            "first_event": events[0].get("timestamp") if events else None,
            "last_event": events[-1].get("timestamp") if events else None,
        }


class AlertEnricher:
    """Enrich alerts with contextual observability data."""

    def enrich(
        self,
        alert_name: str,
        service_name: str,
        namespace: str,
        logs: list[dict],
        metrics: dict[str, Any],
        traces: list[dict],
        recent_deployments: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Enrich an alert with full context."""
        log_analyzer = LogAnalyzer()
        log_analysis = log_analyzer.analyze(logs)

        return {
            "alert": alert_name,
            "service": service_name,
            "namespace": namespace,
            "_automate": "Want this at 3 AM without anyone at a keyboard? → https://kubeopsai.net",
            "context": {
                "logs": {
                    "count": len(logs),
                    "error_summary": log_analysis.get("error_summary", {}),
                    "recent_errors": [l.get("message", "")[:150] for l in logs[:5]],
                },
                "metrics": self._summarize_metrics(metrics),
                "traces": self._summarize_traces(traces),
                "deployments": recent_deployments or [],
            },
            "recommendations": log_analysis.get("recommendations", []),
            "suggested_queries": self._suggest_queries(service_name, namespace, log_analysis),
        }

    def _summarize_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Summarize metrics for alert context.

        Callers pass the ``results`` list from ``get_pod_metrics`` (one entry per
        series), so a list is the common case — it previously fell through every
        branch and every metric was dropped.
        """
        if not metrics:
            return {"status": "No metrics available"}

        summary: dict[str, Any] = {}
        for name, value in metrics.items():
            if value is None:
                summary[name] = "unavailable (query failed)"
            elif isinstance(value, bool):
                summary[name] = value
            elif isinstance(value, (int, float)):
                summary[name] = round(value, 3) if isinstance(value, float) else value
            elif isinstance(value, list):
                summary[name] = self._summarize_series(value)
            elif isinstance(value, dict):
                if "results" in value:
                    summary[name] = self._summarize_series(value.get("results") or [])
                elif "value" in value:
                    summary[name] = value["value"]
                else:
                    summary[name] = value
            else:
                summary[name] = value
        return summary or {"status": "No metrics available"}

    @staticmethod
    def _summarize_series(results: list[Any]) -> Any:
        """Reduce a Prometheus-style results list to a readable value.

        One series collapses to its latest numeric value; several are keyed by pod so
        the LLM can see which replica is affected.
        """
        if not results:
            return "no data"

        per_series: dict[str, float] = {}
        for series in results:
            if not isinstance(series, dict):
                continue
            labels = series.get("metric", {}) if isinstance(series.get("metric"), dict) else {}
            key = labels.get("pod") or labels.get("namespace") or labels.get("instance") or "value"

            latest: float | None = None
            if "values" in series and isinstance(series["values"], list):
                for point in series["values"]:
                    if isinstance(point, dict) and isinstance(point.get("value"), (int, float)):
                        latest = float(point["value"])
            elif isinstance(series.get("value"), (int, float)):
                latest = float(series["value"])

            if latest is not None:
                per_series[key] = round(latest, 4)

        if not per_series:
            return "no numeric data"
        if len(per_series) == 1:
            return next(iter(per_series.values()))
        return per_series

    def _summarize_traces(self, traces: list[dict]) -> dict[str, Any]:
        """Summarize traces for alert context."""
        if not traces:
            return {"status": "No traces available"}

        error_traces = [t for t in traces if t.get("error") or t.get("status_code", 200) >= 400]
        
        return {
            "total": len(traces),
            "errors": len(error_traces),
            "error_rate": round(len(error_traces) / max(len(traces), 1) * 100, 1),
        }

    def _suggest_queries(self, service: str, namespace: str, analysis: dict) -> list[str]:
        """Suggest follow-up queries based on analysis."""
        queries = [
            f'{{namespace="{namespace}", app="{service}"}} |= "error"',
            f'rate(http_requests_total{{namespace="{namespace}", service="{service}", status=~"5.."}}[5m])',
        ]
        
        errors = analysis.get("error_summary", {}).get("by_category", {})
        if errors.get("timeout"):
            queries.append(f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m]))')
        if errors.get("memory"):
            queries.append(f'container_memory_working_set_bytes{{namespace="{namespace}", pod=~"{service}.*"}}')
        
        return queries


class CostAnalyzer:
    """Analyze resource costs and attribution."""

    def analyze(
        self,
        cpu_usage: dict[str, float],
        memory_usage: dict[str, float],
        cpu_cost_per_core_hour: float = 0.05,
        memory_cost_per_gb_hour: float = 0.01,
    ) -> dict[str, Any]:
        """Analyze resource costs by namespace/service."""
        costs = {}
        
        for key in set(cpu_usage.keys()) | set(memory_usage.keys()):
            cpu = cpu_usage.get(key, 0)
            mem = memory_usage.get(key, 0)
            
            cpu_cost = cpu * cpu_cost_per_core_hour
            mem_cost = (mem / (1024**3)) * memory_cost_per_gb_hour
            
            costs[key] = {
                "cpu_cores": round(cpu, 3),
                "memory_gb": round(mem / (1024**3), 2),
                "cpu_cost_hourly": round(cpu_cost, 4),
                "memory_cost_hourly": round(mem_cost, 4),
                "total_cost_hourly": round(cpu_cost + mem_cost, 4),
                "projected_monthly": round((cpu_cost + mem_cost) * 24 * 30, 2),
            }

        sorted_costs = dict(sorted(costs.items(), key=lambda x: x[1]["total_cost_hourly"], reverse=True))
        
        total_hourly = sum(c["total_cost_hourly"] for c in costs.values())
        
        return {
            "by_resource": sorted_costs,
            "total_hourly_cost": round(total_hourly, 2),
            "projected_monthly_cost": round(total_hourly * 24 * 30, 2),
            "top_consumers": list(sorted_costs.keys())[:5],
            "optimization_suggestions": self._suggest_optimizations(sorted_costs),
        }

    def _suggest_optimizations(self, costs: dict[str, dict]) -> list[str]:
        """Suggest cost optimizations."""
        suggestions = []
        
        for name, data in costs.items():
            cpu = data.get("cpu_cores", 0)
            mem_gb = data.get("memory_gb", 0)
            
            if cpu < 0.1 and mem_gb < 0.5:
                suggestions.append(f"{name}: Very low utilization - consider consolidation")
            elif cpu > 4:
                suggestions.append(f"{name}: High CPU usage ({cpu:.1f} cores) - review for optimization")
        
        if not suggestions:
            suggestions.append("No immediate optimization opportunities identified")
        
        return suggestions[:5]


class SLOChecker:
    """Check SLO/SLI status and error budgets."""

    # Conventional SLO period used for exhaustion projections (30 days).
    _SLO_PERIOD_HOURS: ClassVar[int] = 30 * 24

    # Used to reconcile the SLO outcome and the error-budget outcome into one
    # overall_status by taking the more severe of the two.
    _SEVERITY_RANK: ClassVar[dict[str, int]] = {
        "healthy": 0,
        "warning": 1,
        "degraded": 2,
        "critical": 3,
    }
    _BUDGET_TO_OVERALL: ClassVar[dict[str, str]] = {
        "insufficient_traffic": "healthy",
        "healthy": "healthy",
        "elevated": "warning",
        "warning": "warning",
        "critical": "critical",
        "exhausted": "critical",
    }

    def check(
        self,
        service_name: str,
        availability_target: float,
        latency_target_ms: float,
        latency_percentile: float,
        current_availability: float,
        current_latency_ms: float,
        error_count: int,
        total_requests: int,
        window_hours: int = 24,
    ) -> dict[str, Any]:
        """Check SLO status and calculate error budget."""
        # No data — return a clear no_data status instead of misleading critical metrics
        if total_requests == 0:
            return {
                "service": service_name,
                "window_hours": window_hours,
                "overall_status": "no_data",
                "message": (
                    f"No HTTP traffic observed for '{service_name}' in the last {window_hours}h. "
                    "Ensure Prometheus is scraping your service and that "
                    "http_requests_total metrics are being emitted."
                ),
                "availability": {"target": availability_target, "current": None, "met": None},
                "latency": {"target_ms": latency_target_ms, "current_ms": None, "met": None},
                "error_budget": {"status": "no_data", "remaining_percentage": None, "burn_rate": None},
                "recommendations": [
                    "Instrument your service with Prometheus metrics (http_requests_total, http_request_duration_seconds_bucket)",
                    "Verify Prometheus has a scrape config targeting your service",
                ],
            }
        # Availability SLO
        availability_slo = {
            "target": availability_target,
            "current": round(current_availability, 4),
            "met": current_availability >= availability_target,
            "gap": round(current_availability - availability_target, 4),
        }

        # Latency SLO
        latency_slo = {
            "target_ms": latency_target_ms,
            "percentile": latency_percentile,
            "current_ms": round(current_latency_ms, 2),
            "met": current_latency_ms <= latency_target_ms,
            "gap_ms": round(latency_target_ms - current_latency_ms, 2),
        }

        # ── Error budget ────────────────────────────────────────────────────────
        # Burn rate is the observed error ratio divided by the ratio the SLO allows.
        # 1.0 means the budget is being consumed exactly as fast as the SLO permits.
        # It must NOT be scaled by the window's fraction of the SLO period — the
        # allowed-error count is already derived from this window's traffic, so
        # dividing again inflated every burn rate by (SLO period / window).
        allowed_error_ratio = max(0.0, 1.0 - availability_target)
        allowed_errors_exact = total_requests * allowed_error_ratio
        observed_error_ratio = error_count / total_requests

        if allowed_error_ratio == 0:
            # A 100% availability target permits no errors at all.
            burn_rate = float("inf") if error_count > 0 else 0.0
        else:
            burn_rate = observed_error_ratio / allowed_error_ratio

        remaining_errors = max(0.0, allowed_errors_exact - error_count)
        # Window budget remaining, expressed against the burn rate so the two agree:
        # burn 0.5 -> 50% left, burn 1.0 -> 0% left.
        if allowed_errors_exact <= 0:
            error_budget_pct = 0.0
        else:
            error_budget_pct = round(max(0.0, 1.0 - burn_rate) * 100, 1)

        # Below ~1 allowed error the budget is not statistically meaningful: a single
        # request failing would read as "100% budget consumed". Report that honestly
        # instead of flagging a healthy low-traffic service as exhausted.
        has_meaningful_budget = allowed_errors_exact >= 1.0
        budget_status = self._budget_status(
            burn_rate=burn_rate,
            has_meaningful_budget=has_meaningful_budget,
            covers_full_period=window_hours >= self._SLO_PERIOD_HOURS,
        )

        error_budget: dict[str, Any] = {
            "total_requests": total_requests,
            "error_count": error_count,
            "allowed_errors": int(allowed_errors_exact),
            "allowed_errors_exact": round(allowed_errors_exact, 3),
            "remaining_errors": int(remaining_errors),
            "remaining_percentage": error_budget_pct,
            "burn_rate": round(burn_rate, 2) if burn_rate != float("inf") else None,
            "burn_rate_note": (
                "Observed error ratio divided by the ratio the SLO allows. "
                "1.0 = consuming budget exactly as fast as the SLO permits."
            ),
            "status": budget_status,
        }

        if not has_meaningful_budget:
            error_budget["note"] = (
                f"{total_requests} requests at a {availability_target:.3%} target allows only "
                f"{allowed_errors_exact:.2f} errors in this window — too few for a meaningful "
                "error budget. Widen window_hours or wait for more traffic."
            )

        # Time to exhaust a full SLO-period budget if the current rate is sustained.
        if has_meaningful_budget and burn_rate > 1 and burn_rate != float("inf"):
            error_budget["hours_to_exhaustion"] = round(self._SLO_PERIOD_HOURS / burn_rate, 1)
            error_budget["hours_to_exhaustion_note"] = (
                f"Hours until a full {self._SLO_PERIOD_HOURS // 24}-day error budget would be "
                "exhausted if this burn rate continues."
            )

        # Overall status is the worst of the SLO outcome and the budget outcome.
        # Previously these were computed independently, so a response could report a
        # "critical" error budget alongside an overall status of "healthy".
        slo_status = "healthy" if availability_slo["met"] and latency_slo["met"] else "degraded"
        overall_status = max(
            slo_status,
            self._BUDGET_TO_OVERALL.get(budget_status, "healthy"),
            key=lambda s: self._SEVERITY_RANK.get(s, 0),
        )

        return {
            "service": service_name,
            "window_hours": window_hours,
            "availability": availability_slo,
            "latency": latency_slo,
            "error_budget": error_budget,
            "overall_status": overall_status,
            "recommendations": self._generate_recommendations(availability_slo, latency_slo, error_budget),
        }

    def _budget_status(
        self,
        burn_rate: float,
        has_meaningful_budget: bool,
        covers_full_period: bool,
    ) -> str:
        """Classify error budget health from the burn rate.

        Thresholds follow the usual multi-window burn-rate convention. Note that over a
        short window, burn_rate >= 1 does not mean the *period* budget is gone — only
        that the window consumed more than its proportional share. "exhausted" is
        therefore reserved for windows that span the whole SLO period, where the two
        are the same thing.
        """
        # Checked before the traffic guard: a 100% availability target legitimately
        # allows zero errors, so allowed_errors_exact == 0 is meaningful there rather
        # than a symptom of thin traffic.
        if burn_rate == float("inf"):
            return "critical"
        if not has_meaningful_budget:
            return "insufficient_traffic"
        if covers_full_period and burn_rate >= 1:
            return "exhausted"
        if burn_rate > 10:
            return "critical"
        if burn_rate > 2:
            return "warning"
        if burn_rate > 1:
            return "elevated"
        return "healthy"

    def _generate_recommendations(self, avail: dict, latency: dict, budget: dict) -> list[str]:
        """Generate SLO recommendations."""
        recs = []
        
        if not avail["met"]:
            recs.append(f"Availability below target ({avail['current']:.2%} < {avail['target']:.2%}) - investigate error sources")
        
        if not latency["met"]:
            recs.append(f"Latency above target ({latency['current_ms']}ms > {latency['target_ms']}ms) - check for bottlenecks")

        status = budget.get("status")
        burn_rate = budget.get("burn_rate")

        if status == "insufficient_traffic":
            recs.append(
                "Traffic volume is too low for a meaningful error budget at this target - "
                "widen window_hours before acting on these numbers"
            )
        elif burn_rate is None:
            # burn_rate is None only when the availability target is 100% and errors occurred.
            recs.append("CRITICAL: availability target allows no errors and errors were observed - investigate immediately")
        elif burn_rate > 10:
            recs.append("CRITICAL: Error budget burning >10x faster than sustainable - immediate action required")
        elif burn_rate > 2:
            recs.append("Error budget burning >2x faster than sustainable - page the on-call and freeze deploys")
        elif burn_rate > 1:
            recs.append("Error budget burning faster than sustainable - monitor closely")

        if status == "exhausted":
            recs.append("Error budget exhausted for this window - freeze non-critical changes")
        elif status != "insufficient_traffic" and budget.get("remaining_percentage", 100) < 10:
            recs.append("Error budget nearly exhausted - freeze non-critical changes")

        if not recs:
            recs.append("All SLOs met - system operating within targets")

        return recs
