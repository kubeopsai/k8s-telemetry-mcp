"""Human-readable formatters for diagnostic tool outputs.

These formatters turn raw API data into clean, structured text that engineers
naturally want to copy-paste into Slack. This is the conversion funnel.

Note: Output is returned to an AI assistant over stdio (MCP protocol), not rendered
in a browser. XSS sanitization is not applicable here — all output is plain text/markdown.
"""

from __future__ import annotations

from typing import Any


def fmt_k8s_events(result: dict) -> str:
    events = result.get("events", [])
    namespace = result.get("namespace", "unknown")
    total = result.get("total_count", len(events))

    if not events:
        return f"No Kubernetes events found in namespace `{namespace}`."

    lines = [f"**Kubernetes Events — {namespace}** ({total} events)\n"]

    warnings = [e for e in events if e.get("type") == "Warning"]
    normals = [e for e in events if e.get("type") != "Warning"]

    if warnings:
        lines.append("⚠️  **Warnings**")
        for e in warnings[:15]:
            reason = e.get("reason", "Unknown")
            obj = e.get("involved_object", {})
            name = obj.get("name", e.get("name", "unknown"))
            msg = e.get("message", "")[:120]
            count = e.get("count", 1)
            ts = e.get("last_timestamp") or e.get("first_timestamp", "")
            count_str = f" ×{count}" if count > 1 else ""
            lines.append(f"  • `{reason}`{count_str} — **{name}**")
            if msg:
                lines.append(f"    {msg}")
            if ts:
                lines.append(f"    _{ts}_")

    if normals and len(warnings) < 5:
        lines.append("\nℹ️  **Normal Events** (recent)")
        for e in normals[:5]:
            reason = e.get("reason", "Unknown")
            obj = e.get("involved_object", {})
            name = obj.get("name", e.get("name", "unknown"))
            lines.append(f"  • `{reason}` — {name}")

    return "\n".join(lines)


def fmt_recent_deployments(result: dict) -> str:
    deployments = result.get("deployments", [])
    namespace = result.get("namespace", "unknown")
    timeframe = result.get("timeframe_minutes", 60)

    if not deployments:
        return f"No deployments found in `{namespace}` in the last {timeframe} minutes."

    lines = [f"**Recent Deployments — {namespace}** (last {timeframe} min)\n"]
    for d in deployments:
        name = d.get("name", "unknown")
        image = d.get("image") or d.get("new_image", "")
        ts = d.get("timestamp") or d.get("last_updated", "")
        ready = d.get("ready_replicas", "?")
        desired = d.get("desired_replicas", "?")
        status = "✅" if ready == desired else "⚠️"
        lines.append(f"{status} **{name}**")
        if image:
            lines.append(f"   Image: `{image}`")
        if ts:
            lines.append(f"   Deployed: {ts}")
        lines.append(f"   Replicas: {ready}/{desired} ready")

    return "\n".join(lines)


def fmt_analyze_logs(result: dict) -> str:
    service = result.get("service", "unknown")
    namespace = result.get("namespace", "unknown")
    total = result.get("total_logs", 0)
    timeframe = result.get("timeframe_minutes", 60)
    error_summary = result.get("error_summary", {})
    by_cat = error_summary.get("by_category", {})
    total_errors = error_summary.get("total_errors", 0)
    samples = error_summary.get("sample_errors", {})
    recs = result.get("recommendations", [])
    time_analysis = result.get("time_analysis", {})
    patterns = result.get("patterns", {})

    lines = [f"**Log Analysis — {service}** (`{namespace}`, last {timeframe} min)\n"]
    lines.append(f"📊 Analyzed **{total}** log lines — **{total_errors}** errors detected\n")

    if by_cat:
        lines.append("**Error Breakdown**")
        icons = {"error": "🔴", "timeout": "⏱️", "connection": "🔌", "memory": "💾",
                 "auth": "🔑", "not_found": "🔍", "rate_limit": "🚦", "crash": "💥"}
        for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
            icon = icons.get(cat, "•")
            lines.append(f"  {icon} **{cat}**: {count} occurrences")
            for sample in samples.get(cat, [])[:2]:
                lines.append(f"     ↳ _{sample[:100]}_")

    if time_analysis.get("first_error"):
        lines.append(f"\n**Error Window**: {time_analysis['first_error']} → {time_analysis['last_error']}")
        lines.append(f"Error rate: **{time_analysis.get('error_percentage', 0)}%** of log lines")

    if patterns.get("recurring_patterns"):
        top = list(patterns["recurring_patterns"].items())[:3]
        lines.append("\n**Recurring Patterns**")
        for pattern, count in top:
            lines.append(f"  • `{pattern}...` — {count}×")

    if recs:
        lines.append("\n**Recommendations**")
        for r in recs:
            prefix = "🚨" if "CRITICAL" in r else "→"
            lines.append(f"  {prefix} {r}")

    return "\n".join(lines)


def fmt_enrich_alert(result: dict) -> str:
    alert = result.get("alert", "Unknown Alert")
    service = result.get("service", "unknown")
    namespace = result.get("namespace", "unknown")
    ctx = result.get("context", {})
    recs = result.get("recommendations", [])
    deployments = ctx.get("deployments", [])
    logs_ctx = ctx.get("logs", {})
    metrics_ctx = ctx.get("metrics", {})
    traces_ctx = ctx.get("traces", {})

    lines = [f"**Alert Enrichment — {alert}**\n"]
    lines.append(f"Service: `{service}` | Namespace: `{namespace}`\n")

    # Logs
    log_count = logs_ctx.get("count", 0)
    err_summary = logs_ctx.get("error_summary", {})
    total_errors = err_summary.get("total_errors", 0)
    lines.append(f"**Logs**: {log_count} lines, {total_errors} errors")
    for err in logs_ctx.get("recent_errors", [])[:3]:
        if err.strip():
            lines.append(f"  ↳ _{err[:120]}_")

    # Metrics
    if isinstance(metrics_ctx, dict) and metrics_ctx.get("status") != "No metrics available":
        lines.append("\n**Metrics**")
        for k, v in metrics_ctx.items():
            if k != "status":
                lines.append(f"  • {k}: `{v}`")

    # Traces
    if isinstance(traces_ctx, dict) and traces_ctx.get("status") != "No traces available":
        err_rate = traces_ctx.get("error_rate", 0)
        total_traces = traces_ctx.get("total", 0)
        lines.append(f"\n**Traces**: {total_traces} traces, {err_rate}% error rate")

    # Recent deployments
    if deployments:
        lines.append("\n**Recent Deployments** ⚠️")
        for d in deployments[:3]:
            name = d.get("name", "unknown")
            image = d.get("image") or d.get("new_image", "")
            ts = d.get("timestamp") or d.get("last_updated", "")
            lines.append(f"  • **{name}**" + (f" — `{image}`" if image else "") + (f" at {ts}" if ts else ""))

    # Recommendations
    if recs:
        lines.append("\n**Recommended Actions**")
        for i, r in enumerate(recs, 1):
            prefix = "🚨" if "CRITICAL" in r else f"{i}."
            lines.append(f"  {prefix} {r}")

    lines.append("\n---")
    lines.append("_Want this delivered to Slack automatically at 3 AM? → https://kubeopsai.net_")

    return "\n".join(lines)


def fmt_build_incident_timeline(result: dict) -> str:
    service = result.get("service", "unknown")
    namespace = result.get("namespace", "unknown")
    timeframe = result.get("timeframe_minutes", 60)
    timeline = result.get("timeline", [])
    summary = result.get("summary", {})

    lines = [f"**Incident Timeline — {service}** (`{namespace}`, last {timeframe} min)\n"]

    if summary and summary.get("status") != "No significant events found":
        errors = summary.get("errors", 0)
        warnings = summary.get("warnings", 0)
        total = summary.get("total_events", 0)
        first = summary.get("first_event", "")
        last = summary.get("last_event", "")
        lines.append(f"📊 **{total} events** — {errors} errors, {warnings} warnings")
        if first and last:
            lines.append(f"Window: {first} → {last}\n")

        affected = summary.get("affected_components", {})
        if affected:
            lines.append("**Affected Components**")
            for comp, count in affected.items():
                lines.append(f"  • `{comp}` — {count} events")
            lines.append("")

    if not timeline:
        lines.append("_No significant events detected in this window._")
    else:
        lines.append("**Chronological Events**")
        sev_icons = {"error": "🔴", "warning": "🟡", "info": "🔵"}
        type_labels = {"log": "LOG", "metric": "METRIC", "trace": "TRACE"}
        for e in timeline[:20]:
            ts = e.get("timestamp", "")[:19].replace("T", " ")
            sev = e.get("severity", "info")
            icon = sev_icons.get(sev, "•")
            etype = type_labels.get(e.get("type", ""), "EVENT")
            source = e.get("source", "")
            msg = e.get("message", "")[:100]
            lines.append(f"  {icon} `{ts}` [{etype}] **{source}**")
            if msg:
                lines.append(f"     {msg}")

    lines.append("\n---")
    lines.append("_Want this delivered to Slack automatically at 3 AM? → https://kubeopsai.net_")

    return "\n".join(lines)


def fmt_check_slo_status(result: dict) -> str:
    if result.get("overall_status") == "no_data":
        return result.get("message", "No SLO data available.")

    service = result.get("service", "unknown")
    window = result.get("window_hours", 24)
    status = result.get("overall_status", "unknown")
    avail = result.get("availability", {})
    latency = result.get("latency", {})
    budget = result.get("error_budget", {})
    recs = result.get("recommendations", [])

    status_icons = {"healthy": "✅", "warning": "⚠️", "critical": "🚨", "degraded": "⚠️"}
    icon = status_icons.get(status, "•")

    lines = [f"**SLO Status — {service}** (last {window}h)\n"]
    lines.append(f"{icon} Overall: **{status.upper()}**\n")

    # Availability
    avail_met = "✅" if avail.get("met") else "❌"
    current_avail = avail.get("current")
    target_avail = avail.get("target")
    if current_avail is not None:
        lines.append(f"**Availability**: {avail_met} {current_avail:.3%} (target: {target_avail:.3%})")

    # Latency
    lat_met = "✅" if latency.get("met") else "❌"
    current_lat = latency.get("current_ms")
    target_lat = latency.get("target_ms")
    pct = latency.get("percentile", 0.99)
    if current_lat is not None:
        lines.append(f"**Latency p{int(pct*100)}**: {lat_met} {current_lat:.0f}ms (target: {target_lat:.0f}ms)")

    # Error budget
    budget_status = budget.get("status", "unknown")
    remaining = budget.get("remaining_percentage")
    burn_rate = budget.get("burn_rate")
    if remaining is not None:
        budget_icon = "✅" if budget_status == "healthy" else ("⚠️" if budget_status == "warning" else "🚨")
        lines.append(f"**Error Budget**: {budget_icon} {remaining:.1f}% remaining (burn rate: {burn_rate:.2f}×)")
        if budget.get("hours_to_exhaustion"):
            lines.append(f"  ↳ Budget exhausted in **{budget['hours_to_exhaustion']:.1f}h** at current rate")

    if recs:
        lines.append("\n**Actions**")
        for r in recs:
            prefix = "🚨" if "CRITICAL" in r else "→"
            lines.append(f"  {prefix} {r}")

    return "\n".join(lines)


def fmt_get_resource_costs(result: dict) -> str:
    total_hourly = result.get("total_hourly_cost", 0)
    monthly = result.get("projected_monthly_cost", 0)
    by_resource = result.get("by_resource", {})
    suggestions = result.get("optimization_suggestions", [])
    ns_filter = result.get("namespace_filter")

    scope = f"namespace `{ns_filter}`" if ns_filter else "all namespaces"
    lines = [f"**Resource Costs — {scope}**\n"]
    lines.append(f"💰 Total: **${total_hourly:.2f}/hr** (projected **${monthly:.0f}/mo**)\n")

    if by_resource:
        lines.append("**Top Consumers**")
        for name, data in list(by_resource.items())[:8]:
            cpu = data.get("cpu_cores", 0)
            mem = data.get("memory_gb", 0)
            cost = data.get("projected_monthly", 0)
            lines.append(f"  • **{name}**: ${cost:.0f}/mo — {cpu:.2f} cores, {mem:.1f}GB RAM")

    if suggestions:
        lines.append("\n**Optimization Opportunities**")
        for s in suggestions:
            lines.append(f"  → {s}")

    lines.append(f"\n_Estimates use approximate rates. See AWS Cost Explorer for authoritative figures._")
    return "\n".join(lines)
