"""Tests for KubernetesClient.get_pod_status.

This method was moved here from promtops, where it had been reimplemented against
`_k8s._api_client` — an attribute KubernetesClient does not define. Every call raised
AttributeError before reaching its own error handler, so the model received a raw
Python error string from the tool the system prompt told it to use for dependency
checks. The reimplementation also ran the kubernetes client synchronously on the event
loop and skipped redaction of container and condition messages.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from k8s_telemetry_mcp.tools.kubernetes import KubernetesClient


def _container(name="app", state="running", *, message=None, reason=None,
               ready=True, restarts=0, exit_code=None):
    if state == "running":
        st = SimpleNamespace(
            running=SimpleNamespace(started_at="2026-01-01T03:00:00Z"),
            waiting=None, terminated=None,
        )
    elif state == "waiting":
        st = SimpleNamespace(
            running=None,
            waiting=SimpleNamespace(reason=reason or "CrashLoopBackOff", message=message),
            terminated=None,
        )
    else:
        st = SimpleNamespace(
            running=None, waiting=None,
            terminated=SimpleNamespace(
                reason=reason or "OOMKilled", exit_code=exit_code or 137,
                finished_at="2026-01-01T03:14:00Z", message=message,
            ),
        )
    return SimpleNamespace(name=name, ready=ready, restart_count=restarts, state=st)


def _pod(name="payment-api-abc123", phase="Running", containers=None, conditions=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(node_name="ip-10-0-1-5"),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=containers if containers is not None else [_container()],
            conditions=conditions or [],
            start_time="2026-01-01T02:59:00Z",
        ),
    )


@pytest.fixture
def client():
    """A KubernetesClient with the SDK constructors bypassed."""
    c = KubernetesClient.__new__(KubernetesClient)
    c._core = MagicMock()
    c._apps = MagicMock()
    c._autoscaling = MagicMock()
    return c


class TestGetPodStatus:
    async def test_returns_pod_details(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])
        result = await client.get_pod_status(namespace="prod", pod_name="payment-api-abc123")

        assert result["count"] == 1
        assert "error" not in result
        pod = result["pods"][0]
        assert pod["name"] == "payment-api-abc123"
        assert pod["phase"] == "Running"
        assert pod["node"] == "ip-10-0-1-5"

    async def test_exact_name_uses_a_field_selector(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])
        await client.get_pod_status(namespace="prod", pod_name="payment-api-abc123")

        _args, kwargs = client._core.list_namespaced_pod.call_args
        assert kwargs["field_selector"] == "metadata.name=payment-api-abc123"

    async def test_falls_back_to_prefix_match(self, client):
        # Deployment-managed pods carry a generated suffix, so an exact lookup misses.
        client._core.list_namespaced_pod.side_effect = [
            SimpleNamespace(items=[]),                                   # exact: no hit
            SimpleNamespace(items=[_pod("payment-api-abc123"), _pod("other-svc-xyz")]),
        ]
        result = await client.get_pod_status(namespace="prod", pod_name="payment-api")

        assert result["count"] == 1
        assert result["pods"][0]["name"] == "payment-api-abc123"

    async def test_lists_all_pods_when_no_name_given(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod("a"), _pod("b")]
        )
        result = await client.get_pod_status(namespace="prod")

        assert result["count"] == 2
        _args, kwargs = client._core.list_namespaced_pod.call_args
        assert "field_selector" not in kwargs

    async def test_container_waiting_state_is_reported(self, client):
        containers = [_container(state="waiting", reason="CrashLoopBackOff", ready=False, restarts=43)]
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod(containers=containers)])
        result = await client.get_pod_status(namespace="prod")

        c = result["pods"][0]["containers"][0]
        assert c["state"] == "waiting"
        assert c["reason"] == "CrashLoopBackOff"
        assert c["restart_count"] == 43
        assert c["ready"] is False

    async def test_container_terminated_state_includes_exit_code(self, client):
        containers = [_container(state="terminated", reason="OOMKilled", exit_code=137)]
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod(containers=containers)])
        result = await client.get_pod_status(namespace="prod")

        c = result["pods"][0]["containers"][0]
        assert c["state"] == "terminated"
        assert c["exit_code"] == 137
        assert c["reason"] == "OOMKilled"

    async def test_running_container_reports_start_time(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])
        result = await client.get_pod_status(namespace="prod")
        c = result["pods"][0]["containers"][0]
        assert c["state"] == "running"
        assert c["started_at"] == "2026-01-01T03:00:00Z"

    async def test_secrets_in_container_messages_are_redacted(self, client):
        # Waiting/terminated messages can echo mounted secret values or DSNs.
        containers = [_container(
            state="waiting",
            message="failed to connect: postgres://admin:hunter2@db:5432/app",
        )]
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod(containers=containers)])
        result = await client.get_pod_status(namespace="prod")

        message = result["pods"][0]["containers"][0]["message"]
        assert "hunter2" not in message
        assert "REDACTED" in message

    async def test_secrets_in_conditions_are_redacted(self, client):
        conditions = [SimpleNamespace(
            type="Ready", status="False", reason="ContainersNotReady",
            message="secret token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        )]
        client._core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(conditions=conditions)]
        )
        result = await client.get_pod_status(namespace="prod")

        assert "REDACTED" in result["pods"][0]["conditions"][0]["message"]

    async def test_pending_pod_without_container_statuses(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(phase="Pending", containers=[])]
        )
        result = await client.get_pod_status(namespace="prod")

        assert result["pods"][0]["phase"] == "Pending"
        assert result["pods"][0]["containers"] == []

    async def test_empty_namespace_returns_zero_pods(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
        result = await client.get_pod_status(namespace="empty")

        assert result["count"] == 0
        assert result["pods"] == []
        assert "error" not in result

    async def test_permission_error_is_explained_not_silent(self, client):
        # An empty list and "you lack RBAC" must never look the same to the model.
        client._core.list_namespaced_pod.side_effect = RuntimeError("pods is forbidden")
        result = await client.get_pod_status(namespace="prod")

        assert "error" in result
        assert "'get,list' on 'pods'" in result["error"]
        assert result["pods"] == []

    async def test_api_calls_run_off_the_event_loop(self, client):
        seen = {}

        def record(*_a, **_kw):
            seen["thread"] = threading.current_thread().name
            return SimpleNamespace(items=[_pod()])

        client._core.list_namespaced_pod.side_effect = record
        await client.get_pod_status(namespace="prod")

        # The kubernetes SDK is synchronous; it must be dispatched to an executor.
        assert seen["thread"] != "MainThread"

    async def test_pod_filter_and_namespace_are_echoed(self, client):
        client._core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])
        result = await client.get_pod_status(namespace="prod", pod_name="payment-api-abc123")

        assert result["pod_filter"] == "payment-api-abc123"
        assert result["namespace"] == "prod"
