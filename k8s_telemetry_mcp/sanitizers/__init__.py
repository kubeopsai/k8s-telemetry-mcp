"""Log sanitization module - Enterprise security feature for PII/secret redaction."""

import re
from re import Pattern
from typing import Any

# Compiled regex patterns for performance
PATTERNS: list[tuple[str, Pattern[str]]] = [
    # Credit Cards (Visa, MC, Amex, Discover)
    ("CREDIT_CARD", re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b")),
    # SSN
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # AWS Access Keys
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")),
    # AWS Secret Keys
    ("AWS_SECRET", re.compile(r"(?i)(?:aws_secret_access_key|secret_access_key|aws_secret)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?")),
    # Generic API Keys
    ("API_KEY", re.compile(r"(?i)(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?")),
    # Passwords in logs
    ("PASSWORD", re.compile(r"(?i)(?:password|passwd|pwd|secret)\s*[=:]\s*['\"]?([^\s'\"]{4,})['\"]?")),
    # JWT Tokens
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]*\.eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\b")),
    # Bearer Tokens
    ("BEARER", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE)),
    # Private IPs (RFC 1918)
    ("PRIVATE_IP", re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b")),
    # Email Addresses
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    # Phone Numbers
    ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b")),
    # Kubernetes Secrets (base64 encoded)
    ("K8S_SECRET", re.compile(r"(?i)(?:data|stringData):\s*\n(?:\s+[a-zA-Z0-9_-]+:\s*[A-Za-z0-9+/=]{20,}\n?)+")),
    # Database Connection Strings
    ("DB_CONN", re.compile(r"(?i)(?:mongodb|postgres|mysql|redis|amqp)://[^\s]+")),
]


def sanitize(text: str, enabled: bool = True) -> str:
    """Sanitize sensitive data from text.
    
    Args:
        text: The text to sanitize
        enabled: Whether sanitization is enabled
        
    Returns:
        Sanitized text with sensitive data redacted
    """
    if not enabled or not text:
        return text

    result = text
    for name, pattern in PATTERNS:
        result = pattern.sub(f"[REDACTED_{name}]", result)
    return result


_MAX_SANITIZE_DEPTH = 12


def sanitize_structure(value: Any, enabled: bool = True, _depth: int = 0) -> Any:
    """Recursively sanitize every string inside an arbitrarily nested structure.

    Log entries frequently arrive with nested payloads — a JSON-formatted log line
    parsed into objects, a list of dicts, labels inside labels. Walking only the top
    two levels meant anything deeper reached the LLM unredacted.

    Depth is capped to guarantee termination on pathological input; anything below the
    cap is returned untouched rather than silently dropped.
    """
    if not enabled or _depth > _MAX_SANITIZE_DEPTH:
        return value
    if isinstance(value, str):
        return sanitize(value, enabled)
    if isinstance(value, dict):
        return {k: sanitize_structure(v, enabled, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        cleaned = [sanitize_structure(v, enabled, _depth + 1) for v in value]
        return type(value)(cleaned) if isinstance(value, tuple) else cleaned
    return value


def sanitize_logs(logs: list[dict], enabled: bool = True) -> list[dict]:
    """Sanitize a list of log entries at any nesting depth.

    Args:
        logs: List of log entry dictionaries
        enabled: Whether sanitization is enabled

    Returns:
        List of sanitized log entries
    """
    if not enabled:
        return logs
    return [sanitize_structure(entry, enabled) for entry in logs]
