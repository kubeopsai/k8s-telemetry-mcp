"""Loki client for querying logs."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config import settings
from src.sanitizers import sanitize_logs


class LokiClient:
    """Client for querying Loki logs."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or settings.loki_url).rstrip("/")
        self.timeout = timeout or settings.loki_timeout

    async def query_logs(
        self,
        pod_name: str | None = None,
        namespace: str = "default",
        container: str | None = None,
        query: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query logs from Loki.
        
        Args:
            pod_name: Name of the pod (supports regex)
            namespace: Kubernetes namespace
            container: Container name filter
            query: Custom LogQL query (overrides other filters)
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of log lines
            
        Returns:
            List of log entries with timestamp, labels, and message
        """
        limit = min(limit or settings.max_log_lines, settings.max_log_lines)
        end_time = end_time or datetime.now(UTC)
        max_range = timedelta(hours=settings.max_query_range_hours)
        start_time = start_time or (end_time - max_range)

        # Enforce max query range
        if end_time - start_time > max_range:
            start_time = end_time - max_range

        # Build LogQL query
        if query:
            logql = query
        else:
            selectors = [f'namespace="{namespace}"']
            if pod_name:
                selectors.append(f'pod=~"{pod_name}.*"')
            if container:
                selectors.append(f'container="{container}"')
            logql = "{" + ",".join(selectors) + "}"

        params = {
            "query": logql,
            "start": str(int(start_time.timestamp() * 1e9)),
            "end": str(int(end_time.timestamp() * 1e9)),
            "limit": str(limit),
            "direction": "backward",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/loki/api/v1/query_range", params=params)
            response.raise_for_status()
            data = response.json()

        logs = self._parse_response(data)
        return sanitize_logs(logs, settings.enable_sanitization)

    def _parse_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Loki response into structured log entries."""
        logs = []
        results = data.get("data", {}).get("result", [])

        for stream in results:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                logs.append({
                    "timestamp": datetime.fromtimestamp(int(ts) / 1e9, tz=UTC).isoformat(),
                    "labels": labels,
                    "message": line,
                })
        return sorted(logs, key=lambda x: x["timestamp"], reverse=True)
