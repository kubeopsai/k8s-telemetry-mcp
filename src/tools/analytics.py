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
        if metrics:
            for metric_name, data in metrics.items():
                if isinstance(data, dict) and data.get("anomaly"):
                    events.append({
                        "timestamp": data.get("timestamp", ""),
                        "type": "metric",
                        "severity": "warning",
                        "source": metric_name,
                        "message": f"{metric_name}: {data.get('description', 'anomaly detected')}",
                    })

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
        }

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
        """Summarize metrics for alert context."""
        if not metrics:
            return {"status": "No metrics available"}

        summary = {}
        for name, value in metrics.items():
            if isinstance(value, (int, float)):
                summary[name] = round(value, 3) if isinstance(value, float) else value
            elif isinstance(value, dict):
                summary[name] = value.get("value", value)
        return summary

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

        # Error budget calculation
        allowed_errors = int(total_requests * (1 - availability_target))
        error_budget_remaining = max(0, allowed_errors - error_count)
        error_budget_pct = round(error_budget_remaining / max(allowed_errors, 1) * 100, 1)
        
        # Burn rate
        window_fraction = window_hours / (30 * 24)  # Fraction of monthly window
        expected_budget_used = window_fraction * 100
        actual_budget_used = 100 - error_budget_pct
        burn_rate = actual_budget_used / max(expected_budget_used, 0.01)

        error_budget = {
            "total_requests": total_requests,
            "error_count": error_count,
            "allowed_errors": allowed_errors,
            "remaining_errors": error_budget_remaining,
            "remaining_percentage": error_budget_pct,
            "burn_rate": round(burn_rate, 2),
            "status": self._budget_status(error_budget_pct, burn_rate),
        }

        # Time to exhaustion
        if burn_rate > 1 and error_budget_pct > 0:
            hours_to_exhaustion = (error_budget_pct / 100) * (30 * 24) / burn_rate
            error_budget["hours_to_exhaustion"] = round(hours_to_exhaustion, 1)

        overall_status = "healthy" if availability_slo["met"] and latency_slo["met"] else "degraded"
        if error_budget_pct < 10:
            overall_status = "critical"
        elif error_budget_pct < 25:
            overall_status = "warning"

        return {
            "service": service_name,
            "window_hours": window_hours,
            "availability": availability_slo,
            "latency": latency_slo,
            "error_budget": error_budget,
            "overall_status": overall_status,
            "recommendations": self._generate_recommendations(availability_slo, latency_slo, error_budget),
        }

    def _budget_status(self, remaining_pct: float, burn_rate: float) -> str:
        """Determine error budget status."""
        if remaining_pct <= 0:
            return "exhausted"
        if burn_rate > 2:
            return "critical"
        if burn_rate > 1:
            return "warning"
        return "healthy"

    def _generate_recommendations(self, avail: dict, latency: dict, budget: dict) -> list[str]:
        """Generate SLO recommendations."""
        recs = []
        
        if not avail["met"]:
            recs.append(f"Availability below target ({avail['current']:.2%} < {avail['target']:.2%}) - investigate error sources")
        
        if not latency["met"]:
            recs.append(f"Latency above target ({latency['current_ms']}ms > {latency['target_ms']}ms) - check for bottlenecks")
        
        if budget["burn_rate"] > 2:
            recs.append("CRITICAL: Error budget burning 2x faster than sustainable - immediate action required")
        elif budget["burn_rate"] > 1:
            recs.append("Warning: Error budget burning faster than sustainable - monitor closely")
        
        if budget["remaining_percentage"] < 10:
            recs.append("Error budget nearly exhausted - freeze non-critical changes")
        
        if not recs:
            recs.append("All SLOs met - system operating within targets")
        
        return recs
