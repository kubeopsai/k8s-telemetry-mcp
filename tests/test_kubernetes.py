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


# ---------------------------------------------------------------------------
# Node → EC2 instance identity
#
# `provider_id` is the only authoritative link between a Kubernetes node and the cloud
# instance behind it. Incident reconstruction uses it to connect an AWS resource change
# to an in-cluster symptom; a wrong instance id there produces a confident but false
# causal claim, so parsing must refuse rather than guess.
# ---------------------------------------------------------------------------

from k8s_telemetry_mcp.tools.kubernetes import _instance_id_from_provider_id


def _node(name="ip-10-0-1-5", provider_id="aws:///eu-central-1a/i-0abc123def456"):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(provider_id=provider_id),
        status=SimpleNamespace(
            conditions=[SimpleNamespace(type="Ready", status="True", message=None, reason=None)],
            allocatable={"cpu": "2", "memory": "8Gi"},
            capacity={"cpu": "2", "memory": "8Gi"},
        ),
    )


class TestInstanceIdFromProviderId:
    def test_standard_eks_provider_id(self):
        assert _instance_id_from_provider_id("aws:///eu-central-1a/i-0abc123def456") == "i-0abc123def456"

    def test_provider_id_without_a_zone_segment(self):
        assert _instance_id_from_provider_id("aws:///i-0abc123def456") == "i-0abc123def456"

    def test_trailing_slash_is_tolerated(self):
        assert _instance_id_from_provider_id("aws:///eu-central-1a/i-0abc123/") == "i-0abc123"

    @pytest.mark.parametrize("provider_id", [
        None,
        "",
        "gce://project/zone/instance-1",       # different cloud
        "azure:///subscriptions/x/vm-1",       # different cloud
        "aws:///eu-central-1a/",               # empty instance segment
        "aws:///eu-central-1a/fargate-pod-1",  # not an EC2 instance id
    ])
    def test_unparseable_or_non_ec2_returns_none(self, provider_id):
        """Returning None keeps the reconstruction honest; a guess would fabricate a link."""
        assert _instance_id_from_provider_id(provider_id) is None


class TestNodePressureExposesInstanceIdentity:
    @staticmethod
    def _client_with(nodes):
        client = KubernetesClient.__new__(KubernetesClient)
        client._core = MagicMock()
        client._core.list_node = MagicMock(return_value=SimpleNamespace(items=nodes))

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        client._run = _run
        return client

    async def test_node_entries_carry_provider_and_instance_id(self):
        client = self._client_with([_node()])
        result = await client.get_node_pressure()
        node = result["all_nodes"][0]
        assert node["provider_id"] == "aws:///eu-central-1a/i-0abc123def456"
        assert node["instance_id"] == "i-0abc123def456"

    async def test_a_node_without_a_provider_id_reports_none_not_an_error(self):
        client = self._client_with([_node(provider_id=None)])
        result = await client.get_node_pressure()
        node = result["all_nodes"][0]
        assert node["provider_id"] is None
        assert node["instance_id"] is None
        assert node["ready"] is True  # the rest of the payload is unaffected

    async def test_existing_pressure_fields_are_unchanged(self):
        """Guard against the addition altering the shape callers already depend on."""
        client = self._client_with([_node()])
        result = await client.get_node_pressure()
        assert result["total_nodes"] == 1
        assert result["pressure_count"] == 0
        assert result["not_ready_count"] == 0
        for key in ("allocatable_cpu", "capacity_memory", "memory_pressure", "conditions"):
            assert key in result["all_nodes"][0]
