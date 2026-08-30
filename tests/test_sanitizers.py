"""Tests for the redaction layer.

The README and wiki advertise automatic PII/secret redaction as a headline security
feature, but this module had no test coverage in the public repo. These tests pin the
behaviour that is claimed, and document the known limits of a regex-based scrubber.
"""

import pytest

from k8s_telemetry_mcp.sanitizers import sanitize, sanitize_logs, sanitize_structure


class TestSanitizePatterns:
    @pytest.mark.parametrize(
        ("text", "marker"),
        [
            ("key AKIAIOSFODNN7EXAMPLE here", "[REDACTED_AWS_KEY]"),
            ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "[REDACTED_AWS_SECRET]"),
            ("password=hunter2", "[REDACTED_PASSWORD]"),
            ("api_key=abcdefghijklmnopqrstuvwxyz", "[REDACTED_API_KEY]"),
            ("Authorization: Bearer abc.def.ghi", "[REDACTED_BEARER]"),
            ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature", "[REDACTED_JWT]"),
            ("contact ops@example.com now", "[REDACTED_EMAIL]"),
            ("ssn 123-45-6789", "[REDACTED_SSN]"),
            ("card 4111111111111111", "[REDACTED_CREDIT_CARD]"),
            ("postgres://user:pw@db:5432/app", "[REDACTED_DB_CONN]"),
            ("peer 10.1.2.3 refused", "[REDACTED_PRIVATE_IP]"),
        ],
    )
    def test_pattern_is_redacted(self, text, marker):
        assert marker in sanitize(text)

    def test_original_secret_value_does_not_survive(self):
        assert "hunter2" not in sanitize("password=hunter2")

    def test_disabled_returns_input_unchanged(self):
        raw = "password=hunter2"
        assert sanitize(raw, enabled=False) == raw

    def test_empty_string_is_safe(self):
        assert sanitize("") == ""


class TestPrivateIpVsCidrNotation:
    """Found live: an AWS Config security-group diff reported the revoked source range
    as '[REDACTED_PRIVATE_IP]/8' instead of '10.0.0.0/8'. A CIDR network address in a
    security group rule or a CloudTrail request is the evidence a reconstruction reports
    on, not PII — and every real VPC uses an RFC1918 range, so this corrupted the
    flagship "security group tightened" scenario for nearly every real customer."""

    @pytest.mark.parametrize("cidr", [
        "10.0.0.0/8",
        "10.1.2.0/24",
        "172.16.0.0/12",
        "192.168.1.0/24",
    ])
    def test_cidr_notation_survives_intact(self, cidr):
        text = f"ingress from {cidr}"
        result = sanitize(text)
        assert cidr in result
        assert "REDACTED" not in result

    def test_a_bare_host_ip_is_still_redacted(self):
        """Only the CIDR suffix is exempted; a plain IP with no /NN is still PII."""
        assert "[REDACTED_PRIVATE_IP]" in sanitize("peer 10.1.2.3 refused")
        assert "10.1.2.3" not in sanitize("peer 10.1.2.3 refused")

    def test_a_config_item_ip_range_field_is_not_corrupted(self):
        """The exact shape AWS Config returns for a security group rule's source range."""
        payload = {"ipv4Ranges": [{"cidrIp": "10.0.0.0/8", "description": "promtops-live-test"}]}
        result = sanitize_structure(payload)
        assert result["ipv4Ranges"][0]["cidrIp"] == "10.0.0.0/8"

    def test_benign_text_is_untouched(self):
        msg = "Reconciling deployment checkout-api in namespace prod"
        assert sanitize(msg) == msg

    def test_multiple_secrets_in_one_line(self):
        result = sanitize("password=a1b2c3 and AKIAIOSFODNN7EXAMPLE")
        assert "[REDACTED_PASSWORD]" in result
        assert "[REDACTED_AWS_KEY]" in result


class TestSanitizeStructureDepth:
    """The walker previously descended only one level, so nested payloads leaked."""

    def test_top_level_string(self):
        assert "[REDACTED_PASSWORD]" in sanitize_structure({"m": "password=x1y2z3"})["m"]

    def test_nested_dict_two_levels(self):
        out = sanitize_structure({"a": {"b": "password=x1y2z3"}})
        assert "[REDACTED_PASSWORD]" in out["a"]["b"]

    def test_deeply_nested_dict(self):
        payload = {"l1": {"l2": {"l3": {"l4": "AKIAIOSFODNN7EXAMPLE"}}}}
        out = sanitize_structure(payload)
        assert out["l1"]["l2"]["l3"]["l4"] == "[REDACTED_AWS_KEY]"

    def test_list_of_dicts(self):
        out = sanitize_structure([{"m": "password=x1y2z3"}])
        assert "[REDACTED_PASSWORD]" in out[0]["m"]

    def test_dict_containing_list_of_dicts(self):
        out = sanitize_structure({"items": [{"inner": {"m": "password=x1y2z3"}}]})
        assert "[REDACTED_PASSWORD]" in out["items"][0]["inner"]["m"]

    def test_list_of_strings(self):
        out = sanitize_structure(["password=x1y2z3", "fine"])
        assert "[REDACTED_PASSWORD]" in out[0]
        assert out[1] == "fine"

    def test_tuple_type_is_preserved(self):
        out = sanitize_structure(("password=x1y2z3",))
        assert isinstance(out, tuple)
        assert "[REDACTED_PASSWORD]" in out[0]

    def test_non_string_scalars_pass_through(self):
        payload = {"count": 5, "ratio": 1.5, "ok": True, "missing": None}
        assert sanitize_structure(payload) == payload

    def test_recursion_depth_is_bounded(self):
        # Guarantees termination on pathological input rather than blowing the stack.
        payload: dict = {"leaf": "password=x1y2z3"}
        for _ in range(50):
            payload = {"n": payload}
        sanitize_structure(payload)  # must not raise

    def test_disabled_returns_input_unchanged(self):
        payload = {"a": {"b": "password=x1y2z3"}}
        assert sanitize_structure(payload, enabled=False) == payload


class TestSanitizeLogs:
    def test_message_and_labels_are_scrubbed(self):
        logs = [{"message": "password=x1y2z3", "labels": {"pod": "AKIAIOSFODNN7EXAMPLE"}}]
        out = sanitize_logs(logs)
        assert "[REDACTED_PASSWORD]" in out[0]["message"]
        assert out[0]["labels"]["pod"] == "[REDACTED_AWS_KEY]"

    def test_nested_structured_log_payload_is_scrubbed(self):
        logs = [{
            "message": "ok",
            "structured": {"request": {"headers": {"authorization": "Bearer abc.def"}}},
        }]
        out = sanitize_logs(logs)
        assert "[REDACTED_BEARER]" in out[0]["structured"]["request"]["headers"]["authorization"]

    def test_timestamps_and_counts_survive(self):
        logs = [{"timestamp": "2026-01-01T00:00:00Z", "message": "ok", "count": 3}]
        out = sanitize_logs(logs)
        assert out[0]["timestamp"] == "2026-01-01T00:00:00Z"
        assert out[0]["count"] == 3

    def test_empty_list(self):
        assert sanitize_logs([]) == []

    def test_disabled_returns_same_object(self):
        logs = [{"message": "password=x1y2z3"}]
        assert sanitize_logs(logs, enabled=False) is logs
