"""Tempo client for querying distributed traces."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.sanitizers import sanitize

_SENSITIVE_KEY = re.compile(r"(?i)(password|passwd|pwd|secret|token|api.?key|credential)")


class TempoClient:
    """Client for querying Tempo traces."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or settings.tempo_url).rstrip("/")
        self.timeout = timeout or settings.tempo_timeout

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        """Get a trace by ID.
        
        Args:
            trace_id: The trace ID to retrieve
            
        Returns:
            Trace data with spans, or a not-found message
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/traces/{trace_id}")
            if response.status_code == 404:
                return {"trace_id": trace_id, "found": False, "message": f"Trace '{trace_id}' not found in Tempo."}
            response.raise_for_status()
            trace = response.json()

        return self._sanitize_trace(trace)

    async def search_traces(
        self,
        service_name: str | None = None,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        min_duration: str | None = None,
        max_duration: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for traces.
        
        Args:
            service_name: Filter by service name
            operation: Filter by operation name
            tags: Filter by tags (key-value pairs)
            min_duration: Minimum duration (e.g., "100ms")
            max_duration: Maximum duration (e.g., "1s")
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of traces
            
        Returns:
            List of matching traces
        """
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))

        # Build TraceQL query
        conditions = []
        if service_name:
            conditions.append(f'resource.service.name="{service_name}"')
        if operation:
            conditions.append(f'name="{operation}"')
        if tags:
            for k, v in tags.items():
                conditions.append(f'span.{k}="{v}"')
        if min_duration:
            conditions.append(f"duration>{min_duration}")
        if max_duration:
            conditions.append(f"duration<{max_duration}")

        query = "{" + " && ".join(conditions) + "}" if conditions else "{}"

        params = {
            "q": query,
            "start": str(int(start_time.timestamp())),
            "end": str(int(end_time.timestamp())),
            "limit": str(limit),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/search", params=params)
            response.raise_for_status()
            data = response.json()

        traces = data.get("traces", [])
        return [self._sanitize_trace(t) for t in traces]

    def _sanitize_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        """Sanitize sensitive data from trace."""
        if not settings.enable_sanitization:
            return trace

        def sanitize_dict(d: dict, parent_key: str = "") -> dict:
            # Handle OTLP attribute pattern: {"key": "db.password", "value": {"stringValue": "s3cr3t"}}
            # The sibling "key" field tells us whether "value" is sensitive
            otlp_key = d.get("key", "") if isinstance(d.get("key"), str) else ""
            result = {}
            for k, v in d.items():
                effective_key = otlp_key if (k == "value" and otlp_key) else (parent_key if _SENSITIVE_KEY.search(parent_key) else k)
                if isinstance(v, str):
                    if _SENSITIVE_KEY.search(effective_key):
                        result[k] = "[REDACTED_PASSWORD]"
                    else:
                        result[k] = sanitize(v)
                elif isinstance(v, dict):
                    result[k] = sanitize_dict(v, parent_key=effective_key)
                elif isinstance(v, list):
                    result[k] = [sanitize_dict(i, parent_key=effective_key) if isinstance(i, dict) else sanitize(i) if isinstance(i, str) else i for i in v]
                else:
                    result[k] = v
            return result

        return sanitize_dict(trace)
