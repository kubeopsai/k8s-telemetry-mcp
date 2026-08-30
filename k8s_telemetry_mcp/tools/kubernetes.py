"""Kubernetes API client for events, scaling history, node pressure, and deployments.

Requires a ClusterRole granting read access to:
  - events (core API group)
  - horizontalpodautoscalers (autoscaling)
  - nodes (core API group)
  - deployments, replicasets (apps)

See the Helm chart RBAC templates for the exact ClusterRole definition.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from k8s_telemetry_mcp.sanitizers import sanitize

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    _K8S_AVAILABLE = True
except ImportError:
    _K8S_AVAILABLE = False


def _load_k8s_config():
    """Load in-cluster config, fall back to kubeconfig for local dev."""
    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()


def _instance_id_from_provider_id(provider_id: str | None) -> str | None:
    """Extract the cloud instance id from a Kubernetes node providerID.

    EKS emits `aws:///<availability-zone>/<instance-id>`, and some configurations omit
    the zone segment, giving `aws:///<instance-id>`. Both are handled by taking the
    final path segment.

    Returns None for a missing providerID or a non-AWS provider, rather than guessing.
    A wrong instance id would produce a confident but false link between an AWS change
    and an in-cluster symptom, which is worse than no link at all.
    """
    if not provider_id or not provider_id.startswith("aws://"):
        return None
    candidate = provider_id.rstrip("/").rsplit("/", 1)[-1]
    return candidate if candidate.startswith("i-") else None


class KubernetesClient:
    """Client for querying the Kubernetes API directly."""

    def __init__(self):
        if not _K8S_AVAILABLE:
            raise RuntimeError(
                "kubernetes Python package is not installed. "
                "Add 'kubernetes>=28.0.0' to dependencies."
            )
        _load_k8s_config()
        self._core = k8s_client.CoreV1Api()
        self._apps = k8s_client.AppsV1Api()
        self._autoscaling = k8s_client.AutoscalingV2Api()

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_pod_status(
        self,
        namespace: str = "default",
        pod_name: str | None = None,
    ) -> dict[str, Any]:
        """Get phase, conditions and container states for pods in a namespace.

        Answers "is the dependency this service can't reach actually running?" — the
        question that distinguishes a connectivity problem from a dead backend.

        Requires `get,list` on `pods`, which is beyond the ClusterRole the MCP Helm
        chart installs by default. Callers without that permission receive a clear
        error rather than an empty result.

        Args:
            namespace: Kubernetes namespace
            pod_name: Exact pod name, or a name prefix. Omit to list every pod.
        """
        try:
            pods = await self._list_pods(namespace, pod_name)
        except Exception as e:
            return {
                "error": (
                    f"Kubernetes API error: {e}. Ensure the service account has "
                    "'get,list' on 'pods' in this namespace."
                ),
                "pods": [],
            }

        result = [self._describe_pod(pod) for pod in pods]
        return {
            "namespace": namespace,
            "pod_filter": pod_name,
            "pods": result,
            "count": len(result),
        }

    async def _list_pods(self, namespace: str, pod_name: str | None) -> list[Any]:
        """Resolve pods by exact name, then by prefix, else all pods in the namespace."""
        if not pod_name:
            resp = await self._run(self._core.list_namespaced_pod, namespace)
            return list(resp.items)

        resp = await self._run(
            self._core.list_namespaced_pod,
            namespace,
            field_selector=f"metadata.name={pod_name}",
        )
        if resp.items:
            return list(resp.items)

        # Deployment-managed pods carry a generated suffix, so fall back to a prefix
        # match against the full list.
        resp = await self._run(self._core.list_namespaced_pod, namespace)
        return [p for p in resp.items if p.metadata.name.startswith(pod_name)]

    @staticmethod
    def _container_state(container_status: Any) -> dict[str, Any]:
        state = container_status.state
        if state.running:
            detail = {"state": "running", "started_at": str(state.running.started_at)}
        elif state.waiting:
            detail = {
                "state": "waiting",
                "reason": state.waiting.reason,
                # Sanitized: waiting/terminated messages can echo mounted secret values
                # or connection strings.
                "message": sanitize(state.waiting.message or ""),
            }
        elif state.terminated:
            detail = {
                "state": "terminated",
                "reason": state.terminated.reason,
                "exit_code": state.terminated.exit_code,
                "finished_at": str(state.terminated.finished_at),
                "message": sanitize(state.terminated.message or ""),
            }
        else:
            detail = {"state": "unknown"}

        return {
            "name": container_status.name,
            "ready": container_status.ready,
            "restart_count": container_status.restart_count,
            **detail,
        }

    def _describe_pod(self, pod: Any) -> dict[str, Any]:
        return {
            "name": pod.metadata.name,
            "phase": pod.status.phase,
            "node": pod.spec.node_name if pod.spec else None,
            "conditions": [
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": sanitize(c.message or ""),
                }
                for c in (pod.status.conditions or [])
            ],
            "containers": [
                self._container_state(cs) for cs in (pod.status.container_statuses or [])
            ],
            "start_time": str(pod.status.start_time) if pod.status.start_time else None,
        }

    async def get_events(
        self,
        namespace: str = "default",
        pod_name: str | None = None,
        event_type: str | None = None,
        timeframe_minutes: int = 60,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get Kubernetes events for a namespace or specific pod.

        Args:
            namespace: Kubernetes namespace
            pod_name: Filter events for a specific pod (optional)
            event_type: Filter by type: 'Warning' or 'Normal' (optional)
            timeframe_minutes: How far back to look
            limit: Maximum events to return
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=timeframe_minutes)

        try:
            resp = await self._run(
                self._core.list_namespaced_event,
                namespace=namespace,
                limit=500,
            )
        except Exception as e:
            return {"error": f"Kubernetes API error: {e}. Ensure the service account has 'get,list' on 'events'.", "events": []}

        events = []
        for ev in resp.items:
            last_time = ev.last_timestamp or ev.event_time or ev.first_timestamp
            if last_time and last_time.replace(tzinfo=UTC) < cutoff:
                continue
            if event_type and ev.type != event_type:
                continue
            if pod_name and ev.involved_object.name and pod_name not in ev.involved_object.name:
                continue

            events.append({
                "type": ev.type,
                "reason": ev.reason,
                "message": sanitize(ev.message or ""),
                "object_kind": ev.involved_object.kind,
                "object_name": ev.involved_object.name,
                "namespace": namespace,
                "count": ev.count,
                "first_time": ev.first_timestamp.isoformat() if ev.first_timestamp else None,
                "last_time": last_time.isoformat() if last_time else None,
                "source_component": ev.source.component if ev.source else None,
            })

        events.sort(key=lambda x: x["last_time"] or "", reverse=True)
        events = events[:limit]

        warnings = [e for e in events if e["type"] == "Warning"]
        return {
            "namespace": namespace,
            "pod_filter": pod_name,
            "timeframe_minutes": timeframe_minutes,
            "total_events": len(events),
            "warning_count": len(warnings),
            "events": events,
        }

    async def get_scaling_history(
        self,
        namespace: str = "default",
        deployment_name: str | None = None,
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get HPA scaling history and current autoscaler status.

        Requires kube-state-metrics for historical scaling data.
        Falls back to current HPA status if kube-state-metrics is unavailable.

        Args:
            namespace: Kubernetes namespace
            deployment_name: Filter to a specific deployment (optional)
            timeframe_minutes: How far back to look for scaling events
        """
        try:
            resp = await self._run(
                self._autoscaling.list_namespaced_horizontal_pod_autoscaler,
                namespace=namespace,
            )
        except Exception as e:
            return {"error": f"Kubernetes API error: {e}. Ensure the service account has 'get,list' on 'horizontalpodautoscalers'.", "hpas": []}

        hpas = []
        for hpa in resp.items:
            if deployment_name and hpa.spec.scale_target_ref.name != deployment_name:
                continue
            hpas.append({
                "name": hpa.metadata.name,
                "target": hpa.spec.scale_target_ref.name,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas,
                "desired_replicas": hpa.status.desired_replicas,
                "last_scale_time": hpa.status.last_scale_time.isoformat() if hpa.status.last_scale_time else None,
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                    for c in (hpa.status.conditions or [])
                ],
            })

        # Get scaling events from Kubernetes events
        events_result = await self.get_events(
            namespace=namespace,
            timeframe_minutes=timeframe_minutes,
        )
        scaling_events = [
            e for e in events_result.get("events", [])
            if e.get("reason") in ("SuccessfulRescale", "DesiredReplicas", "ScalingReplicaSet")
            and (not deployment_name or deployment_name in (e.get("object_name") or ""))
        ]

        result: dict[str, Any] = {
            "namespace": namespace,
            "deployment_filter": deployment_name,
            "hpas": hpas,
            "recent_scaling_events": scaling_events,
        }

        if not hpas:
            result["note"] = (
                "No HPAs found in this namespace. "
                "kube-state-metrics is required for historical HPA metrics in Prometheus. "
                "Install it via: helm install kube-state-metrics prometheus-community/kube-state-metrics"
            )

        return result

    async def get_node_pressure(self) -> dict[str, Any]:
        """Get node pressure conditions, resource usage, and eviction events."""
        try:
            resp = await self._run(self._core.list_node)
        except Exception as e:
            return {"error": f"Kubernetes API error: {e}. Ensure the service account has 'get,list' on 'nodes'.", "nodes": []}

        nodes = []
        for node in resp.items:
            conditions = {c.type: {"status": c.status, "message": c.message, "reason": c.reason}
                         for c in (node.status.conditions or [])}
            allocatable = node.status.allocatable or {}
            capacity = node.status.capacity or {}

            # provider_id is the only authoritative link between a Kubernetes node and
            # the cloud instance backing it. On EKS it looks like
            # "aws:///eu-central-1a/i-0abc123def456". Without it, an AWS resource change
            # cannot be connected to an in-cluster symptom by anything better than
            # "happened around the same time".
            provider_id = node.spec.provider_id if node.spec else None

            nodes.append({
                "name": node.metadata.name,
                "provider_id": provider_id,
                "instance_id": _instance_id_from_provider_id(provider_id),
                "ready": conditions.get("Ready", {}).get("status") == "True",
                "memory_pressure": conditions.get("MemoryPressure", {}).get("status") == "True",
                "disk_pressure": conditions.get("DiskPressure", {}).get("status") == "True",
                "pid_pressure": conditions.get("PIDPressure", {}).get("status") == "True",
                "allocatable_cpu": allocatable.get("cpu"),
                "allocatable_memory": allocatable.get("memory"),
                "capacity_cpu": capacity.get("cpu"),
                "capacity_memory": capacity.get("memory"),
                "conditions": conditions,
            })

        pressure_nodes = [n for n in nodes if n["memory_pressure"] or n["disk_pressure"] or n["pid_pressure"]]
        not_ready = [n for n in nodes if not n["ready"]]

        return {
            "total_nodes": len(nodes),
            "not_ready_count": len(not_ready),
            "pressure_count": len(pressure_nodes),
            "nodes_with_pressure": pressure_nodes,
            "nodes_not_ready": not_ready,
            "all_nodes": nodes,
        }

    async def get_recent_deployments(
        self,
        namespace: str = "default",
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get recent deployment changes — what was rolled out in the last N minutes.

        Args:
            namespace: Kubernetes namespace
            timeframe_minutes: How far back to look
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=timeframe_minutes)

        try:
            resp = await self._run(
                self._apps.list_namespaced_deployment,
                namespace=namespace,
            )
        except Exception as e:
            return {"error": f"Kubernetes API error: {e}. Ensure the service account has 'get,list' on 'deployments'.", "deployments": []}

        recent = []
        for dep in resp.items:
            # Check if any condition was updated recently
            conditions = dep.status.conditions or []
            latest_condition_time = None
            for c in conditions:
                if c.last_update_time and c.last_update_time.replace(tzinfo=UTC) > cutoff and (
                    latest_condition_time is None or c.last_update_time > latest_condition_time
                ):
                        latest_condition_time = c.last_update_time

            creation_time = dep.metadata.creation_timestamp
            if creation_time:
                creation_time = creation_time.replace(tzinfo=UTC)

            if latest_condition_time or (creation_time and creation_time > cutoff):
                recent.append({
                    "name": dep.metadata.name,
                    "namespace": namespace,
                    "replicas": dep.spec.replicas,
                    "ready_replicas": dep.status.ready_replicas,
                    "updated_replicas": dep.status.updated_replicas,
                    "image": dep.spec.template.spec.containers[0].image if dep.spec.template.spec.containers else None,
                    "last_updated": latest_condition_time.isoformat() if latest_condition_time else None,
                    "created_at": creation_time.isoformat() if creation_time else None,
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason,
                         "message": sanitize(c.message or "")}
                        for c in conditions
                    ],
                })

        recent.sort(key=lambda x: x["last_updated"] or x["created_at"] or "", reverse=True)

        return {
            "namespace": namespace,
            "timeframe_minutes": timeframe_minutes,
            "recent_deployment_count": len(recent),
            "deployments": recent,
        }
