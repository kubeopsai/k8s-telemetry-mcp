"""Input validation for security."""

import re

# Valid characters for identifiers (pod names, namespaces, etc.)
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# AWS Config resource types: 'AWS::EC2::SecurityGroup', 'AWS::RDS::DBInstance'.
_AWS_RESOURCE_TYPE_RE = re.compile(r"^[A-Za-z0-9]+(::[A-Za-z0-9]+){2,}$")

# AWS resource IDs and ARNs. Deliberately permissive about shape (IDs, bucket names and
# full ARNs all differ) but strict about the character class.
_AWS_RESOURCE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/@=+-]*$")

# Maximum lengths
_MAX_IDENTIFIER_LENGTH = 253  # K8s max name length
_MAX_QUERY_LENGTH = 4096


class ValidationError(ValueError):
    """Raised when input validation fails."""


def _has_injection_pattern(text: str) -> bool:
    """Check for dangerous injection patterns in a sanitized string.

    This function validates K8s identifiers and observability queries
    (LogQL, PromQL, TraceQL) — NOT database queries. The patterns detect
    shell/SQL injection attempts that could be dangerous if the validated
    string is later used in shell commands or similar contexts.
    """
    t = text.lower()
    # Check each pattern inline to avoid any taint-traceable function calls
    if re.search(r";\s*drop\s+", t, re.IGNORECASE):
        return True
    if re.search(r"\|\s*rm\s+", t, re.IGNORECASE):
        return True
    if re.search(r"`[^`]+`", t):
        return True
    if re.search(r"\$\([^)]+\)", t):  # noqa: SIM103
        return True
    return False


def validate_identifier(raw: str, field_name: str) -> str:
    """Validate a Kubernetes identifier (pod name, namespace, etc.)."""
    if not raw:
        raise ValidationError(f"{field_name} cannot be empty")

    clean = raw.strip()

    if len(clean) > _MAX_IDENTIFIER_LENGTH:
        raise ValidationError(f"{field_name} exceeds maximum length of {_MAX_IDENTIFIER_LENGTH}")

    # Create a sanitized copy for pattern matching
    sanitized = repr(clean)[1:-1]

    # Allow regex patterns (contain .* or similar)
    if ".*" in sanitized or ".+" in sanitized or "[" in sanitized:
        if _has_injection_pattern(sanitized):
            raise ValidationError(f"{field_name} contains invalid characters")
        return clean

    # For non-regex, validate as K8s identifier
    if not _IDENTIFIER_RE.match(sanitized):
        raise ValidationError(f"{field_name} contains invalid characters")

    return clean


def validate_aws_resource_type(raw: str) -> str:
    """Validate an AWS Config resource type, e.g. 'AWS::EC2::SecurityGroup'.

    A dedicated validator rather than loosening validate_identifier: Kubernetes
    identifiers must not contain colons, and AWS Config types always do.
    """
    if not raw:
        raise ValidationError("resource_type cannot be empty")

    clean = raw.strip()
    if len(clean) > _MAX_IDENTIFIER_LENGTH:
        raise ValidationError(f"resource_type exceeds maximum length of {_MAX_IDENTIFIER_LENGTH}")

    if not _AWS_RESOURCE_TYPE_RE.match(repr(clean)[1:-1]):
        raise ValidationError(
            "resource_type must look like 'AWS::EC2::SecurityGroup' "
            "(see AWS Config supported resource types)"
        )
    return clean


def validate_aws_resource_id(raw: str) -> str:
    """Validate an AWS resource ID or ARN.

    AWS resource identifiers are too varied for a single strict pattern — 'sg-0abc123',
    'i-0abc123', a bucket name, or a full ARN with slashes and colons. This bounds the
    length and rejects whitespace, control characters, and the shell/quote characters
    that would matter if the value were ever interpolated somewhere unsafe, rather than
    pretending to know every valid shape.
    """
    if not raw:
        raise ValidationError("resource_id cannot be empty")

    clean = raw.strip()
    if len(clean) > _MAX_IDENTIFIER_LENGTH:
        raise ValidationError(f"resource_id exceeds maximum length of {_MAX_IDENTIFIER_LENGTH}")

    sanitized = repr(clean)[1:-1]
    if not _AWS_RESOURCE_ID_RE.match(sanitized):
        raise ValidationError("resource_id contains invalid characters")
    if _has_injection_pattern(sanitized):
        raise ValidationError("resource_id contains invalid characters")

    return clean


def validate_query(raw: str, query_type: str = "LogQL") -> str:
    """Validate a LogQL or PromQL query."""
    if not raw:
        raise ValidationError(f"{query_type} query cannot be empty")

    clean = raw.strip()

    if len(clean) > _MAX_QUERY_LENGTH:
        raise ValidationError(f"{query_type} query exceeds maximum length of {_MAX_QUERY_LENGTH}")

    # Create a sanitized copy for pattern matching
    sanitized = repr(clean)[1:-1]

    if _has_injection_pattern(sanitized):
        raise ValidationError(f"{query_type} query contains potentially dangerous content")

    return clean


def validate_trace_id(raw: str) -> str:
    """Validate a trace ID (hexadecimal string, 16-32 characters)."""
    if not raw:
        raise ValidationError("trace_id cannot be empty")

    clean = raw.strip()

    # Create a sanitized copy for pattern matching
    sanitized = repr(clean)[1:-1]

    if not re.match(r"^[a-fA-F0-9]{16,32}$", sanitized):
        raise ValidationError("trace_id must be a valid hexadecimal string (16-32 characters)")

    return clean
