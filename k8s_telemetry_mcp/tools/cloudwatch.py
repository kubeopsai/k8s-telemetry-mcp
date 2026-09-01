"""AWS CloudWatch Logs + Container Insights backend client."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.sanitizers import sanitize, sanitize_logs


class CloudWatchClient:
    """Client for querying CloudWatch Logs and Container Insights metrics."""

    def __init__(
        self,
        log_group: str | None = None,
        region: str | None = None,
        timeout: int = 30,
    ):
        self._log_group = log_group or settings.cloudwatch_log_group
        self._region = region or settings.cloudwatch_region or None
        self._timeout = timeout

    def _logs_client(self):
        return boto3.client("logs", region_name=self._region)

    def _metrics_client(self):
        return boto3.client("cloudwatch", region_name=self._region)

    def _insights_client(self):
        return boto3.client("logs", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        """Run a synchronous boto3 call in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

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
        """Query logs from CloudWatch Logs Insights."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))

        if query:
            cw_query = query
        else:
            filters = [f'kubernetes.namespace_name = "{namespace}"']
            if pod_name:
                filters.append(f'kubernetes.pod_name like "{pod_name}"')
            if container:
                filters.append(f'kubernetes.container_name = "{container}"')
            filter_str = " and ".join(filters)
            cw_query = f"fields @timestamp, @message, kubernetes.pod_name | filter {filter_str} | sort @timestamp desc | limit {limit}"

        client = self._logs_client()
        log_group = self._log_group or f"/aws/containerinsights/{namespace}/application"

        start_resp = await self._run(
            client.start_query,
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=cw_query,
            limit=limit,
        )
        query_id = start_resp["queryId"]

        # Poll until complete
        for _ in range(30):
            await asyncio.sleep(1)
            result = await self._run(client.get_query_results, queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break

        logs = []
        for row in result.get("results", []):
            row_dict = {f["field"]: f["value"] for f in row}
            logs.append({
                "timestamp": row_dict.get("@timestamp", ""),
                "labels": {"pod": row_dict.get("kubernetes.pod_name", "")},
                "message": row_dict.get("@message", ""),
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
        """Query pod metrics from CloudWatch Container Insights."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(minutes=30))

        # Container Insights metric mappings
        metric_map = {
            "cpu": ("pod_cpu_utilization", "Percent"),
            "memory": ("pod_memory_working_set", "Bytes"),
            "restarts": ("pod_number_of_container_restarts", "Count"),
            "network_rx": ("pod_network_rx_bytes", "Bytes/Second"),
            "network_tx": ("pod_network_tx_bytes", "Bytes/Second"),
        }

        if metric_type not in metric_map:
            raise ValueError(f"Unknown metric type: {metric_type}")

        metric_name, unit = metric_map[metric_type]
        client = self._metrics_client()

        error: str | None = None
        try:
            resp = await self._run(
                client.get_metric_statistics,
                Namespace="ContainerInsights",
                MetricName=metric_name,
                Dimensions=[
                    {"Name": "ClusterName", "Value": namespace},
                    {"Name": "PodName", "Value": pod_name},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=["Average"],
                Unit=unit,
            )
            datapoints = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
            results = [
                {
                    "metric": {"pod": pod_name},
                    "timestamp": dp["Timestamp"].isoformat(),
                    "value": dp["Average"],
                }
                for dp in datapoints
            ]
        except ClientError as e:
            # A denied/throttled call must not look identical to "no datapoints in
            # range" — the same failure mode already fixed for CloudTrail and AWS
            # Config elsewhere in this package. `results` stays empty for backward
            # compatibility; the new `error` key is what lets a caller tell the two
            # apart instead of silently reading zero data as "nothing happened".
            results = []
            error = f"{e.response['Error']['Code']}: {e.response['Error']['Message']}"

        payload: dict[str, Any] = {
            "metric_type": metric_type, "pod": pod_name, "namespace": namespace, "results": results,
        }
        if error:
            payload["error"] = error
        return payload

    async def get_cluster_health(self) -> dict[str, Any]:
        """Get cluster health from CloudWatch Container Insights."""
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=10)
        client = self._metrics_client()
        errors: dict[str, str] = {}

        async def _get(metric_name: str) -> float | None:
            try:
                resp = await self._run(
                    client.get_metric_statistics,
                    Namespace="ContainerInsights",
                    MetricName=metric_name,
                    Dimensions=[],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=600,
                    Statistics=["Average"],
                )
                dps = resp.get("Datapoints", [])
                return dps[-1]["Average"] if dps else None
            except ClientError as e:
                # Distinguish "the call failed" from "the call succeeded with no
                # datapoints" — both previously returned None here, so a permission
                # or throttling error on one metric was invisible to the caller.
                errors[metric_name] = f"{e.response['Error']['Code']}: {e.response['Error']['Message']}"
                return None

        node_count = await _get("cluster_node_count")
        pod_count = await _get("cluster_pod_count")
        cpu_util = await _get("node_cpu_utilization")
        mem_util = await _get("node_memory_utilization")

        result: dict[str, Any] = {
            "node_count": int(node_count) if node_count is not None else None,
            "pod_count": int(pod_count) if pod_count is not None else None,
            "running_pods": int(pod_count) if pod_count is not None else None,
            "failed_pods": None,
            "pending_pods": None,
            "cpu_utilization": round(cpu_util / 100, 3) if cpu_util is not None else None,
            "memory_utilization": round(mem_util / 100, 3) if mem_util is not None else None,
            "backend": "cloudwatch",
        }
        if errors:
            result["errors"] = errors
        return result

    async def query_raw(self, query: str, start_time: datetime | None = None, end_time: datetime | None = None) -> list[dict[str, Any]]:
        """Execute a raw CloudWatch Logs Insights query."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))
        log_group = self._log_group
        if not log_group:
            return [{"error": "MCP_CLOUDWATCH_LOG_GROUP is not configured"}]

        client = self._logs_client()
        start_resp = await self._run(
            client.start_query,
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
            limit=100,
        )
        query_id = start_resp["queryId"]
        for _ in range(30):
            await asyncio.sleep(1)
            result = await self._run(client.get_query_results, queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break

        logs = []
        for row in result.get("results", []):
            row_dict = {f["field"]: f["value"] for f in row}
            msg = row_dict.get("@message", "")
            if settings.enable_sanitization:
                msg = sanitize(msg)
            logs.append({"timestamp": row_dict.get("@timestamp", ""), "message": msg})
        return logs
