"""Prometheus client for querying metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from k8s_telemetry_mcp.config import settings


class PrometheusClient:
    """Client for querying Prometheus metrics."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")
        self.timeout = timeout or settings.prometheus_timeout

    async def query_instant(self, query: str, time: datetime | None = None) -> list[dict[str, Any]]:
        """Execute an instant query.
        
        Args:
            query: PromQL query
            time: Evaluation timestamp (defaults to now)
            
        Returns:
            List of metric results
        """
        params = {"query": query}
        if time:
            params["time"] = str(time.timestamp())

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/v1/query", params=params)
            response.raise_for_status()
            return self._parse_response(response.json())

    async def query_range(
        self,
        query: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        step: str = "1m",
    ) -> list[dict[str, Any]]:
        """Execute a range query.
        
        Args:
            query: PromQL query
            start_time: Start of time range
            end_time: End of time range
            step: Query resolution step
            
        Returns:
            List of metric results with values over time
        """
        end_time = end_time or datetime.now(UTC)
        max_range = timedelta(hours=settings.max_query_range_hours)
        start_time = start_time or (end_time - timedelta(hours=1))

        if end_time - start_time > max_range:
            start_time = end_time - max_range

        params = {
            "query": query,
            "start": str(start_time.timestamp()),
            "end": str(end_time.timestamp()),
            "step": step,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/v1/query_range", params=params)
            response.raise_for_status()
            return self._parse_response(response.json())

    async def get_pod_metrics(
        self,
        pod_name: str,
        namespace: str = "default",
        metric_type: str = "cpu",
    ) -> dict[str, Any]:
        """Get common pod metrics.
        
        Args:
            pod_name: Name of the pod (supports regex)
            namespace: Kubernetes namespace
            metric_type: Type of metric (cpu, memory, restarts, network)
            
        Returns:
            Metric data for the pod
        """
        queries = {
            "cpu": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{pod_name}.*"}}[5m])) by (pod)',
            "memory": f'sum(container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{pod_name}.*"}}) by (pod)',
            "restarts": f'sum(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod=~"{pod_name}.*"}}) by (pod)',
            "network_rx": f'sum(rate(container_network_receive_bytes_total{{namespace="{namespace}",pod=~"{pod_name}.*"}}[5m])) by (pod)',
            "network_tx": f'sum(rate(container_network_transmit_bytes_total{{namespace="{namespace}",pod=~"{pod_name}.*"}}[5m])) by (pod)',
        }

        query = queries.get(metric_type)
        if not query:
            raise ValueError(f"Unknown metric type: {metric_type}. Valid: {list(queries.keys())}")

        results = await self.query_instant(query)
        return {"metric_type": metric_type, "pod": pod_name, "namespace": namespace, "results": results}

    async def get_cluster_health(self) -> dict[str, Any]:
        """Get overall cluster health metrics."""
        # kube_pod_status_phase emits a row per pod per phase with value 0 or 1.
        # sum() adds up the 1s to get the actual count in each phase.
        # count() would count all rows regardless of value, giving wrong results.
        queries = {
            "node_count": "count(kube_node_info)",
            "pod_count": "count(kube_pod_info)",
            "running_pods": 'sum(kube_pod_status_phase{phase="Running"})',
            "failed_pods": 'sum(kube_pod_status_phase{phase="Failed"})',
            "pending_pods": 'sum(kube_pod_status_phase{phase="Pending"})',
            "cpu_utilization": 'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[5m]))',
            "memory_utilization": "avg(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))",
        }

        results = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for name, query in queries.items():
                try:
                    response = await client.get(f"{self.base_url}/api/v1/query", params={"query": query})
                    response.raise_for_status()
                    data = self._parse_response(response.json())
                    # None means no pods in that phase (e.g. 0 failed pods) — return 0 not None
                    results[name] = data[0]["value"] if data else 0
                except (httpx.HTTPError, KeyError, IndexError):
                    results[name] = None
        return results

    def _parse_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Prometheus response."""
        results = []
        for item in data.get("data", {}).get("result", []):
            metric = item.get("metric", {})
            if "value" in item:
                ts, val = item["value"]
                results.append({
                    "metric": metric,
                    "timestamp": datetime.fromtimestamp(ts, tz=UTC).isoformat(),
                    "value": float(val) if val != "NaN" else None,
                })
            elif "values" in item:
                values = [
                    {"timestamp": datetime.fromtimestamp(ts, tz=UTC).isoformat(), "value": float(v) if v != "NaN" else None}
                    for ts, v in item["values"]
                ]
                results.append({"metric": metric, "values": values})
        return results
