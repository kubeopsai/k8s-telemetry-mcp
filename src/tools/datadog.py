"""Datadog backend client for logs and metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config import settings
from src.sanitizers import sanitize, sanitize_logs


class DatadogClient:
    """Client for querying Datadog logs and metrics."""

    def __init__(
        self,
        api_key: str | None = None,
        app_key: str | None = None,
        site: str | None = None,
        timeout: int = 30,
    ):
        self._api_key = api_key or settings.datadog_api_key
        self._app_key = app_key or settings.datadog_app_key
        self._site = site or settings.datadog_site
        self._timeout = timeout
        self._base = f"https://api.{self._site}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "DD-API-KEY": self._api_key,
            "DD-APPLICATION-KEY": self._app_key,
            "Content-Type": "application/json",
        }

    async def query_logs(
        self,
        pod_name: str | None = None,
        namespace: str = "default",
        container: str | None = None,
        query: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query logs from Datadog Log Management."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))

        if query:
            dd_query = query
        else:
            parts = [f"kube_namespace:{namespace}"]
            if pod_name:
                parts.append(f"pod_name:{pod_name}*")
            if container:
                parts.append(f"kube_container_name:{container}")
            dd_query = " ".join(parts)

        payload = {
            "filter": {
                "query": dd_query,
                "from": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "page": {"limit": min(limit, 1000)},
            "sort": "-timestamp",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base}/api/v2/logs/events/search",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        logs = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            logs.append({
                "timestamp": attrs.get("timestamp", ""),
                "labels": attrs.get("tags", {}),
                "message": attrs.get("message", ""),
            })

        return sanitize_logs(logs, settings.enable_sanitization)

    async def query_metrics(
        self,
        pod_name: str,
        namespace: str = "default",
        metric_type: str = "cpu",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Query pod metrics from Datadog."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(minutes=30))

        queries = {
            "cpu": f"avg:kubernetes.cpu.usage.total{{kube_namespace:{namespace},pod_name:{pod_name}*}} by {{pod_name}}",
            "memory": f"avg:kubernetes.memory.working_set{{kube_namespace:{namespace},pod_name:{pod_name}*}} by {{pod_name}}",
            "restarts": f"sum:kubernetes.containers.restarts{{kube_namespace:{namespace},pod_name:{pod_name}*}} by {{pod_name}}",
            "network_rx": f"avg:kubernetes.network.rx_bytes{{kube_namespace:{namespace},pod_name:{pod_name}*}} by {{pod_name}}",
            "network_tx": f"avg:kubernetes.network.tx_bytes{{kube_namespace:{namespace},pod_name:{pod_name}*}} by {{pod_name}}",
        }

        query = queries.get(metric_type)
        if not query:
            raise ValueError(f"Unknown metric type: {metric_type}")

        params = {
            "query": query,
            "from": int(start_time.timestamp()),
            "to": int(end_time.timestamp()),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base}/api/v1/query",
                headers=self._headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for series in data.get("series", []):
            results.append({
                "metric": {"pod": series.get("scope", pod_name)},
                "values": [
                    {"timestamp": datetime.fromtimestamp(p[0], tz=UTC).isoformat(), "value": p[1]}
                    for p in series.get("pointlist", [])
                ],
            })

        return {"metric_type": metric_type, "pod": pod_name, "namespace": namespace, "results": results}

    async def get_cluster_health(self) -> dict[str, Any]:
        """Get cluster health metrics from Datadog."""
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=10)
        ts = (int(start_time.timestamp()), int(end_time.timestamp()))

        async def _query(q: str) -> float | None:
            params = {"query": q, "from": ts[0], "to": ts[1]}
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self._base}/api/v1/query", headers=self._headers, params=params)
                r.raise_for_status()
                series = r.json().get("series", [])
                if series and series[0].get("pointlist"):
                    return series[0]["pointlist"][-1][1]
            return None

        node_count = await _query("sum:kubernetes.nodes.by_kubelet{{*}}")
        pod_count = await _query("sum:kubernetes.pods.running{{*}}")
        cpu_util = await _query("avg:kubernetes.cpu.usage.total{{*}} / avg:kubernetes.cpu.limits{{*}}")
        mem_util = await _query("avg:kubernetes.memory.working_set{{*}} / avg:kubernetes.memory.limits{{*}}")

        return {
            "node_count": int(node_count) if node_count is not None else None,
            "pod_count": int(pod_count) if pod_count is not None else None,
            "running_pods": int(pod_count) if pod_count is not None else None,
            "failed_pods": None,
            "pending_pods": None,
            "cpu_utilization": round(cpu_util, 3) if cpu_util is not None else None,
            "memory_utilization": round(mem_util, 3) if mem_util is not None else None,
            "backend": "datadog",
        }

    async def query_raw(self, query: str, start_time: datetime | None = None, end_time: datetime | None = None) -> list[dict[str, Any]]:
        """Execute a raw Datadog log search query."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))
        payload = {
            "filter": {
                "query": query,
                "from": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "page": {"limit": 100},
            "sort": "-timestamp",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base}/api/v2/logs/events/search",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        logs = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            msg = sanitize(attrs.get("message", "")) if settings.enable_sanitization else attrs.get("message", "")
            logs.append({"timestamp": attrs.get("timestamp", ""), "message": msg})
        return logs
