"""AWS Lambda client for function error rates and configuration history."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings


class LambdaClient:
    """Client for querying Lambda function errors and recent configuration changes.

    Lambda incidents fall into two categories:
    1. Runtime errors — throttles, timeouts, out-of-memory, unhandled exceptions.
       These show up in CloudWatch metrics and are surfaced here.
    2. Configuration changes — new code deployed, environment variable changed,
       concurrency limit modified, layer updated. These show up in CloudTrail
       (UpdateFunctionCode, UpdateFunctionConfiguration, PublishVersion) and in
       AWS Config history for AWS::Lambda::Function.
    """

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _lambda_client(self):
        return boto3.client("lambda", region_name=self._region)

    def _cw_client(self):
        return boto3.client("cloudwatch", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_function_errors(
        self,
        function_name: str,
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get error, throttle, and timeout metrics for a Lambda function.

        Returns CloudWatch metric statistics for the window. A spike in Errors,
        Throttles, or Duration approaching the timeout is a symptom event.

        Args:
            function_name: Lambda function name or ARN.
            timeframe_minutes: How far back to look.
        """
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=timeframe_minutes)
        period = max(60, timeframe_minutes * 60 // 20)

        cw = self._cw_client()
        metric_names = ["Errors", "Throttles", "Duration", "Invocations", "ConcurrentExecutions"]
        metrics: dict[str, Any] = {}

        for metric_name in metric_names:
            stat = "Sum" if metric_name in ("Errors", "Throttles", "Invocations") else "Average"
            try:
                resp = await self._run(
                    cw.get_metric_statistics,
                    Namespace="AWS/Lambda",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=period,
                    Statistics=[stat, "Maximum"],
                )
                dps = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
                if dps:
                    metrics[metric_name] = {
                        "latest": round(dps[-1].get(stat, 0), 4),
                        "maximum": round(dps[-1].get("Maximum", 0), 4),
                        "total_datapoints": len(dps),
                        "peak_timestamp": dps[-1]["Timestamp"].isoformat(),
                    }
            except ClientError:
                pass

        # Derive error rate if both Errors and Invocations are available
        error_rate = None
        if "Errors" in metrics and "Invocations" in metrics:
            invocations = metrics["Invocations"]["latest"]
            errors = metrics["Errors"]["latest"]
            if invocations > 0:
                error_rate = round(errors / invocations, 4)

        return {
            "function_name": function_name,
            "timeframe_minutes": timeframe_minutes,
            "metrics": metrics,
            "error_rate": error_rate,
            "note": (
                "For configuration changes (code deploys, env var updates, concurrency changes), "
                "query CloudTrail for UpdateFunctionCode / UpdateFunctionConfiguration events, "
                "or use get_configuration_history with resource_type=AWS::Lambda::Function."
            ),
        }

    async def get_function_config(
        self,
        function_name: str,
    ) -> dict[str, Any]:
        """Get current Lambda function configuration.

        Useful for establishing the current state after a CloudTrail event shows
        a configuration change — what does the function look like right now?

        Args:
            function_name: Lambda function name or ARN.
        """
        client = self._lambda_client()

        try:
            resp = await self._run(client.get_function_configuration, FunctionName=function_name)
        except ClientError as e:
            return {"error": f"Lambda get_function_configuration failed: {e.response['Error']['Message']}"}

        last_modified = resp.get("LastModified")
        return {
            "function_name": resp.get("FunctionName"),
            "function_arn": resp.get("FunctionArn"),
            "runtime": resp.get("Runtime"),
            "handler": resp.get("Handler"),
            "code_size": resp.get("CodeSize"),
            "description": resp.get("Description"),
            "timeout": resp.get("Timeout"),
            "memory_size": resp.get("MemorySize"),
            "last_modified": last_modified,
            "code_sha256": resp.get("CodeSha256"),
            "version": resp.get("Version"),
            "environment_variable_keys": list((resp.get("Environment") or {}).get("Variables", {}).keys()),
            "layers": [
                {"arn": layer.get("Arn"), "code_size": layer.get("CodeSize")}
                for layer in resp.get("Layers", [])
            ],
            "reserved_concurrency": resp.get("ReservedConcurrentExecutions"),
            "state": resp.get("State"),
            "state_reason": resp.get("StateReason"),
            "last_update_status": resp.get("LastUpdateStatus"),
            "last_update_status_reason": resp.get("LastUpdateStatusReason"),
        }
