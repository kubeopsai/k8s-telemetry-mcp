"""Tests for fmt_k8s_events.

Found live, running against a real EKS cluster: this formatter read
result.get("involved_object", {}) and e.get("last_timestamp"), neither of which
get_k8s_events actually returns — the real shape is flat "object_name"/"object_kind"
and "last_time"/"first_time". Because dict.get() on a missing key returns a default
rather than raising, this was never a crash. It silently rendered every event with no
name and no timestamp, which for a formatter with zero test coverage went unnoticed
until a real payload was read directly.

The rest of format.py (fmt_analyze_logs, fmt_build_incident_timeline,
fmt_check_slo_status, fmt_enrich_alert, fmt_get_resource_costs,
fmt_recent_deployments) has no test coverage either. Only fmt_k8s_events is covered
here, because it is the function the live bug was found in; the others are a known
gap, not something this file claims to close.
"""

from k8s_telemetry_mcp.tools.format import fmt_k8s_events


def _event(object_name="checkout-api-7d9f", object_kind="Pod", reason="Unhealthy",
           type_="Warning", message="Readiness probe failed", count=1,
           last_time="2026-08-30T18:53:15+00:00", first_time="2026-08-30T18:40:00+00:00"):
    """The real, flat shape get_k8s_events returns — confirmed against a live cluster."""
    return {
        "type": type_,
        "reason": reason,
        "message": message,
        "object_kind": object_kind,
        "object_name": object_name,
        "count": count,
        "last_time": last_time,
        "first_time": first_time,
    }


def _payload(events, namespace="prod", total_events=None):
    return {
        "namespace": namespace,
        "total_events": total_events if total_events is not None else len(events),
        "events": events,
    }


class TestFmtK8sEvents:
    def test_no_events_says_so_plainly(self):
        result = fmt_k8s_events(_payload([]))
        assert "No Kubernetes events found" in result
        assert "prod" in result

    def test_the_real_object_name_and_reason_appear(self):
        """This is the exact bug: the previous version read involved_object.name, which
        does not exist on this payload, and silently fell through to 'unknown'."""
        result = fmt_k8s_events(_payload([_event()]))
        assert "checkout-api-7d9f" in result
        assert "Unhealthy" in result
        assert "unknown" not in result.lower()

    def test_the_real_timestamp_appears(self):
        """The previous version read last_timestamp/first_timestamp, which do not exist
        on this payload, so no timestamp ever rendered."""
        result = fmt_k8s_events(_payload([_event(last_time="2026-08-30T18:53:15+00:00")]))
        assert "2026-08-30T18:53:15+00:00" in result

    def test_a_repeated_warning_shows_its_count(self):
        result = fmt_k8s_events(_payload([_event(count=24)]))
        assert "×24" in result

    def test_a_single_occurrence_shows_no_count_suffix(self):
        result = fmt_k8s_events(_payload([_event(count=1)]))
        assert "×1" not in result

    def test_warnings_and_normal_events_are_both_rendered(self):
        result = fmt_k8s_events(_payload([
            _event(type_="Warning", reason="Unhealthy"),
            _event(type_="Normal", reason="Pulled", object_name="checkout-api-7d9f"),
        ]))
        assert "Warnings" in result
        assert "Unhealthy" in result
        assert "Pulled" in result

    def test_normal_events_are_omitted_once_there_are_many_warnings(self):
        """Five or more warnings means normal events are not worth the space."""
        warnings = [_event(type_="Warning", reason=f"Warn{i}") for i in range(5)]
        normal = [_event(type_="Normal", reason="Pulled")]
        result = fmt_k8s_events(_payload(warnings + normal))
        assert "Normal Events" not in result

    def test_the_total_event_count_is_reported(self):
        result = fmt_k8s_events(_payload([_event(), _event(object_name="other-pod")], total_events=2))
        assert "2 events" in result

    def test_a_missing_message_does_not_render_an_empty_line(self):
        payload = _payload([_event(message="")])
        result = fmt_k8s_events(payload)
        assert "checkout-api-7d9f" in result
