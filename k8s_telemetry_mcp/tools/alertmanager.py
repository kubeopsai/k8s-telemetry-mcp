"""Prometheus Alertmanager client for alert history and silences."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.sanitizers import sanitize

logger = logging.getLogger(__name__)


class AlertmanagerClient:
    """Client for querying Prometheus Alertmanager."""

    def __init__(self, base_url: str | None = None, timeout: int = 30):
        self.base_url = (base_url or settings.alertmanager_url).rstrip("/")
        self.timeout = timeout

    async def get_alert_history(
        self,
        service_name: str | None = None,
        namespace: str | None = None,
        timeframe_minutes: int = 60,
        include_silences: bool = True,
    ) -> dict[str, Any]:
        """Get recent alerts and active silences from Alertmanager.

        Args:
            service_name: Filter alerts by service name (optional)
            namespace: Filter alerts by namespace label (optional)
            timeframe_minutes: How far back to look for alerts
            include_silences: Whether to include active silences
        """
        if not self.base_url:
            return {
                "error": "Alertmanager is not configured. Set MCP_ALERTMANAGER_URL to enable this tool.",
                "alerts": [],
            }

        cutoff = datetime.now(UTC) - timedelta(minutes=timeframe_minutes)
        results: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Get active and recent alerts
            try:
                resp = await client.get(f"{self.base_url}/api/v2/alerts")
                resp.raise_for_status()
                alerts = resp.json()

                filtered = []
                for alert in alerts:
                    labels = alert.get("labels", {})
                    # Filter by service/namespace if specified
                    if service_name and service_name not in str(labels):
                        continue
                    if namespace and labels.get("namespace") != namespace:
                        continue

                    starts_at = alert.get("startsAt", "")
                    try:
                        alert_time = datetime.fromisoformat(starts_at)
                        if alert_time < cutoff:
                            continue
                except Exception as exc:
                        logger.debug("Failed to parse alert timestamp: %s", exc)

                    filtered.append({
                        "alertname": labels.get("alertname"),
                        "severity": labels.get("severity"),
                        "namespace": labels.get("namespace"),
                        "service": labels.get("service") or labels.get("job"),
                        "status": alert.get("status", {}).get("state"),
                        "starts_at": starts_at,
                        "ends_at": alert.get("endsAt"),
                        "summary": sanitize(alert.get("annotations", {}).get("summary", "")),
                        "description": sanitize(alert.get("annotations", {}).get("description", "")[:300]),
                        "inhibited_by": alert.get("status", {}).get("inhibitedBy", []),
                        "silenced_by": alert.get("status", {}).get("silencedBy", []),
                    })

                results["alerts"] = filtered
                results["alert_count"] = len(filtered)

            except httpx.ConnectError:
                return {
                    "error": f"Cannot connect to Alertmanager at {self.base_url}. Verify MCP_ALERTMANAGER_URL.",
                    "alerts": [],
                }
            except httpx.HTTPStatusError as e:
                return {"error": f"Alertmanager returned HTTP {e.response.status_code}", "alerts": []}

            # Get active silences
            if include_silences:
                try:
                    resp = await client.get(f"{self.base_url}/api/v2/silences")
                    resp.raise_for_status()
                    silences = resp.json()
                    active_silences = [
                        {
                            "id": s.get("id"),
                            "status": s.get("status", {}).get("state"),
                            "created_by": s.get("createdBy"),
                            "comment": sanitize(s.get("comment", "")),
                            "starts_at": s.get("startsAt"),
                            "ends_at": s.get("endsAt"),
                            "matchers": s.get("matchers", []),
                        }
                        for s in silences
                        if s.get("status", {}).get("state") == "active"
                    ]
                    results["active_silences"] = active_silences
                    results["silence_count"] = len(active_silences)
                except Exception as exc:
                    logger.debug("Failed to fetch Alertmanager silences: %s", exc)
                    results["active_silences"] = []
                    results["silence_count"] = 0

        results["timeframe_minutes"] = timeframe_minutes
        return results
