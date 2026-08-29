"""Duration string validation for Prometheus/Tempo query parameters."""

from k8s_telemetry_mcp.validation import ValidationError

_VALID_UNITS: tuple[str, ...] = ("ns", "us", "\u00b5s", "ms", "s", "m", "h")
_ALLOWED_CHARS: frozenset[str] = frozenset("0123456789.ns\u00b5umsh")


def validate_duration(raw: str | None, field_name: str) -> str | None:
    """Validate a Prometheus/Tempo duration string (e.g. '100ms', '1s', '5m').

    Args:
        raw: The raw duration value from user input.
        field_name: Field name used in error messages.

    Returns:
        The validated duration string, or None if raw is empty/None.

    Raises:
        ValidationError: If the value is not a valid duration.
    """
    if not raw:
        return None

    _err = f"{field_name} must be a valid duration (e.g., '100ms', '1s', '5m')"

    # Whitelist check — reject anything outside digits, dot, and unit chars
    for ch in raw:
        if ch not in _ALLOWED_CHARS:
            raise ValidationError(_err)

    # Match unit suffix (longest first to avoid 's' matching before 'ms')
    matched_unit = ""
    for unit in sorted(_VALID_UNITS, key=len, reverse=True):
        unit_len = len(unit)
        if len(raw) > unit_len and raw[-unit_len:] == unit:
            matched_unit = unit
            break

    if not matched_unit:
        raise ValidationError(_err)

    numeric_part = raw[: len(raw) - len(matched_unit)]
    if not numeric_part:
        raise ValidationError(_err)

    try:
        float(numeric_part)
    except ValueError:
        raise ValidationError(_err)

    return raw
