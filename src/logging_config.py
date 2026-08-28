"""Logging configuration for K8s Telemetry MCP Server."""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Context variable for request tracking
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(""),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        for key in ["tool", "duration_ms", "pod", "namespace", "query", "error_type"]:
            if hasattr(record, key):
                log_data[key] = getattr(record, key)

        return json.dumps(log_data)


class AuditLogger:
    """Audit logger for tracking tool invocations."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def log_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """Log a tool invocation for audit purposes."""
        extra = {
            "tool": tool_name,
            "duration_ms": round(duration_ms, 2),
        }

        if success:
            self.logger.info(
                f"Tool '{tool_name}' completed successfully",
                extra=extra,
            )
        else:
            extra["error_type"] = type(error).__name__ if error else "Unknown"
            self.logger.warning(
                f"Tool '{tool_name}' failed: {error}",
                extra=extra,
            )


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid.uuid4())[:8]


def setup_logging(log_level: str = "INFO", json_format: bool = True) -> logging.Logger:
    """Configure logging for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON format for production, text for development
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("k8s-telemetry-mcp")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(getattr(logging, log_level.upper()))

    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s (%(request_id)s): %(message)s",
                defaults={"request_id": ""},
            )
        )

    logger.addHandler(handler)

    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logger


def get_audit_logger() -> AuditLogger:
    """Get the audit logger instance."""
    logger = logging.getLogger("k8s-telemetry-mcp.audit")
    return AuditLogger(logger)
