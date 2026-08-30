"""AWS ECS client for task and service change history."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings


class ECSClient:
    """Client for querying ECS service deployments and task lifecycle events."""

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _client(self):
        return boto3.client("ecs", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_service_deployments(
        self,
        cluster: str,
        service: str,
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get recent deployments for an ECS service.

        Each deployment carries the task definition revision, desired/running/pending
        counts, and the rollout timestamps — the ECS equivalent of a Kubernetes
        rollout history entry.

        Args:
            cluster: ECS cluster name or ARN.
            service: ECS service name or ARN.
            timeframe_minutes: How far back to look.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=timeframe_minutes)
        client = self._client()

        try:
            resp = await self._run(
                client.describe_services,
                cluster=cluster,
                services=[service],
            )
        except ClientError as e:
            return {"error": f"ECS describe_services failed: {e.response['Error']['Message']}", "deployments": []}

        services = resp.get("services", [])
        if not services:
            return {"error": f"ECS service '{service}' not found in cluster '{cluster}'.", "deployments": []}

        svc = services[0]
        raw_deployments = svc.get("deployments", [])

        deployments = []
        for d in raw_deployments:
            created_at = d.get("createdAt")
            if created_at and created_at.replace(tzinfo=UTC) < cutoff:
                continue
            deployments.append({
                "deployment_id": d.get("id"),
                "status": d.get("status"),
                "task_definition": d.get("taskDefinition"),
                "desired_count": d.get("desiredCount"),
                "running_count": d.get("runningCount"),
                "pending_count": d.get("pendingCount"),
                "failed_tasks": d.get("failedTasks", 0),
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": d.get("updatedAt").isoformat() if d.get("updatedAt") else None,
                "rollout_state": d.get("rolloutState"),
                "rollout_state_reason": d.get("rolloutStateReason"),
            })

        return {
            "cluster": cluster,
            "service": service,
            "service_status": svc.get("status"),
            "task_definition": svc.get("taskDefinition"),
            "deployments": deployments,
            "deployment_count": len(deployments),
        }

    async def get_stopped_tasks(
        self,
        cluster: str,
        service: str | None = None,
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get recently stopped ECS tasks with their stop reasons.

        Stopped tasks are the ECS equivalent of Kubernetes OOMKilled/CrashLoop events —
        the primary symptom signal for ECS workloads.

        Args:
            cluster: ECS cluster name or ARN.
            service: Scope to a specific service (optional).
            timeframe_minutes: How far back to look.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=timeframe_minutes)
        client = self._client()

        try:
            kwargs: dict[str, Any] = {
                "cluster": cluster,
                "desiredStatus": "STOPPED",
                "maxResults": 100,
            }
            if service:
                kwargs["serviceName"] = service

            list_resp = await self._run(client.list_tasks, **kwargs)
            task_arns = list_resp.get("taskArns", [])
        except ClientError as e:
            return {"error": f"ECS list_tasks failed: {e.response['Error']['Message']}", "tasks": []}

        if not task_arns:
            return {"cluster": cluster, "service": service, "tasks": [], "count": 0}

        try:
            desc_resp = await self._run(
                client.describe_tasks,
                cluster=cluster,
                tasks=task_arns[:100],
            )
        except ClientError as e:
            return {"error": f"ECS describe_tasks failed: {e.response['Error']['Message']}", "tasks": []}

        tasks = []
        for t in desc_resp.get("tasks", []):
            stopped_at = t.get("stoppedAt")
            if stopped_at and stopped_at.replace(tzinfo=UTC) < cutoff:
                continue
            tasks.append({
                "task_arn": t.get("taskArn"),
                "task_definition": t.get("taskDefinitionArn"),
                "last_status": t.get("lastStatus"),
                "stop_code": t.get("stopCode"),
                "stopped_reason": t.get("stoppedReason"),
                "started_at": t.get("startedAt").isoformat() if t.get("startedAt") else None,
                "stopped_at": stopped_at.isoformat() if stopped_at else None,
                "containers": [
                    {
                        "name": c.get("name"),
                        "last_status": c.get("lastStatus"),
                        "exit_code": c.get("exitCode"),
                        "reason": c.get("reason"),
                    }
                    for c in t.get("containers", [])
                ],
            })

        return {
            "cluster": cluster,
            "service": service,
            "tasks": tasks,
            "count": len(tasks),
        }
