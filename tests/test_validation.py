"""Tests for input validation — validate_identifier, validate_query, validate_trace_id, validate_duration."""

import pytest

from src.duration import validate_duration
from src.validation import ValidationError, validate_identifier, validate_query, validate_trace_id


# ---------------------------------------------------------------------------
# validate_identifier
# ---------------------------------------------------------------------------

class TestValidateIdentifier:
    def test_valid_simple(self):
        assert validate_identifier("my-pod", "pod") == "my-pod"

    def test_valid_with_dots(self):
        assert validate_identifier("my.pod.name", "pod") == "my.pod.name"

    def test_valid_with_numbers(self):
        assert validate_identifier("pod123", "pod") == "pod123"

    def test_valid_regex_pattern(self):
        assert validate_identifier("payment-.*", "pod") == "payment-.*"

    def test_valid_regex_with_brackets(self):
        assert validate_identifier("pod[0-9]", "pod") == "pod[0-9]"

    def test_strips_whitespace(self):
        assert validate_identifier("  my-pod  ", "pod") == "my-pod"

    def test_empty_raises(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_identifier("", "pod")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_identifier("   ", "pod")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            validate_identifier("a" * 254, "pod")

    def test_exactly_max_length_ok(self):
        result = validate_identifier("a" * 253, "pod")
        assert len(result) == 253

    def test_starts_with_special_char_raises(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_identifier("-bad-start", "pod")

    def test_injection_drop_raises(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_identifier("pod; drop table", "pod")

    def test_injection_rm_raises(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_identifier("pod | rm -rf", "pod")

    def test_injection_backtick_raises(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_identifier("`whoami`", "pod")

    def test_injection_subshell_raises(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_identifier("$(cat /etc/passwd)", "pod")

    def test_namespace_valid(self):
        assert validate_identifier("monitoring", "namespace") == "monitoring"

    def test_field_name_in_error(self):
        with pytest.raises(ValidationError, match="my_field"):
            validate_identifier("", "my_field")


# ---------------------------------------------------------------------------
# validate_query
# ---------------------------------------------------------------------------

class TestValidateQuery:
    def test_valid_logql(self):
        q = '{namespace="default"} |= "error"'
        assert validate_query(q, "LogQL") == q

    def test_valid_promql(self):
        q = 'rate(http_requests_total{status="500"}[5m])'
        assert validate_query(q, "PromQL") == q

    def test_strips_whitespace(self):
        assert validate_query("  up  ", "PromQL") == "up"

    def test_empty_raises(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_query("", "LogQL")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            validate_query("a" * 4097, "LogQL")

    def test_exactly_max_length_ok(self):
        result = validate_query("a" * 4096, "LogQL")
        assert len(result) == 4096

    def test_injection_drop_raises(self):
        with pytest.raises(ValidationError, match="dangerous"):
            validate_query("up; drop table foo", "PromQL")

    def test_injection_rm_raises(self):
        with pytest.raises(ValidationError, match="dangerous"):
            validate_query("up | rm -rf /", "PromQL")

    def test_injection_backtick_raises(self):
        with pytest.raises(ValidationError, match="dangerous"):
            validate_query("`id`", "LogQL")

    def test_injection_subshell_raises(self):
        with pytest.raises(ValidationError, match="dangerous"):
            validate_query("$(whoami)", "LogQL")

    def test_query_type_in_error(self):
        with pytest.raises(ValidationError, match="PromQL"):
            validate_query("", "PromQL")


# ---------------------------------------------------------------------------
# validate_trace_id
# ---------------------------------------------------------------------------

class TestValidateTraceId:
    def test_valid_16_chars(self):
        assert validate_trace_id("abcdef1234567890") == "abcdef1234567890"

    def test_valid_32_chars(self):
        tid = "a" * 32
        assert validate_trace_id(tid) == tid

    def test_valid_uppercase(self):
        assert validate_trace_id("ABCDEF1234567890") == "ABCDEF1234567890"

    def test_strips_whitespace(self):
        assert validate_trace_id("  abcdef1234567890  ") == "abcdef1234567890"

    def test_empty_raises(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_trace_id("")

    def test_too_short_raises(self):
        with pytest.raises(ValidationError, match="valid hexadecimal"):
            validate_trace_id("abc123")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError, match="valid hexadecimal"):
            validate_trace_id("a" * 33)

    def test_non_hex_raises(self):
        with pytest.raises(ValidationError, match="valid hexadecimal"):
            validate_trace_id("zzzzzzzzzzzzzzzz")

    def test_15_chars_raises(self):
        with pytest.raises(ValidationError):
            validate_trace_id("abcdef123456789")


# ---------------------------------------------------------------------------
# validate_duration
# ---------------------------------------------------------------------------

class TestValidateDuration:
    def test_none_returns_none(self):
        assert validate_duration(None, "dur") is None

    def test_empty_returns_none(self):
        assert validate_duration("", "dur") is None

    def test_milliseconds(self):
        assert validate_duration("100ms", "dur") == "100ms"

    def test_seconds(self):
        assert validate_duration("5s", "dur") == "5s"

    def test_minutes(self):
        assert validate_duration("10m", "dur") == "10m"

    def test_hours(self):
        assert validate_duration("2h", "dur") == "2h"

    def test_nanoseconds(self):
        assert validate_duration("500ns", "dur") == "500ns"

    def test_microseconds(self):
        assert validate_duration("200us", "dur") == "200us"

    def test_float_value(self):
        assert validate_duration("1.5s", "dur") == "1.5s"

    def test_no_unit_raises(self):
        from src.validation import ValidationError
        with pytest.raises(ValidationError):
            validate_duration("100", "dur")

    def test_invalid_unit_raises(self):
        from src.validation import ValidationError
        with pytest.raises(ValidationError):
            validate_duration("100x", "dur")

    def test_invalid_chars_raises(self):
        from src.validation import ValidationError
        with pytest.raises(ValidationError):
            validate_duration("1s; rm -rf", "dur")

    def test_no_numeric_part_raises(self):
        from src.validation import ValidationError
        with pytest.raises(ValidationError):
            validate_duration("ms", "dur")
