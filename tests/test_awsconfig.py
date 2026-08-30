"""Tests for the AWS Config collector.

`diff_configurations` is the factual backbone of incident reconstruction: it states
what changed on a resource as a matter of record, so the engine built on top does not
have to ask a model to describe a change. Its determinism matters more than its
cleverness, which is what these tests pin.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.tools.awsconfig import (
    _MAX_DIFF_VALUE_CHARS,
    AWSConfigClient,
    diff_configurations,
)


def _paths(changes: list[dict]) -> list[str]:
    return [c["path"] for c in changes]


class TestDiffConfigurations:
    def test_identical_configs_produce_no_diff(self):
        config = {"groupId": "sg-1", "ipPermissions": [{"fromPort": 443}]}
        assert diff_configurations(config, config) == []

    def test_scalar_modification(self):
        changes = diff_configurations({"state": "available"}, {"state": "modifying"})
        assert changes == [{
            "path": "state", "change": "modified",
            "from": "available", "to": "modifying",
        }]

    def test_added_key(self):
        changes = diff_configurations({}, {"newField": "x"})
        assert changes[0]["change"] == "added"
        assert changes[0]["from"] is None
        assert changes[0]["to"] == "x"

    def test_removed_key(self):
        changes = diff_configurations({"gone": "y"}, {})
        assert changes[0]["change"] == "removed"
        assert changes[0]["to"] is None

    def test_nested_path_is_dotted(self):
        before = {"a": {"b": {"c": 1}}}
        after = {"a": {"b": {"c": 2}}}
        assert _paths(diff_configurations(before, after)) == ["a.b.c"]

    def test_list_index_is_bracketed(self):
        before = {"rules": [{"port": 80}]}
        after = {"rules": [{"port": 8080}]}
        assert _paths(diff_configurations(before, after)) == ["rules[0].port"]

    def test_list_growth_is_an_addition(self):
        changes = diff_configurations({"rules": [1]}, {"rules": [1, 2]})
        assert changes[0]["path"] == "rules[1]"
        assert changes[0]["change"] == "added"

    def test_list_shrink_is_a_removal(self):
        changes = diff_configurations({"rules": [1, 2]}, {"rules": [1]})
        assert changes[0]["path"] == "rules[1]"
        assert changes[0]["change"] == "removed"

    def test_a_real_security_group_ingress_opening(self):
        # The canonical incident: someone opens a port to the world.
        before = {"ipPermissions": [{"fromPort": 443, "ipRanges": ["10.0.0.0/8"]}]}
        after = {"ipPermissions": [{"fromPort": 443, "ipRanges": ["0.0.0.0/0"]}]}
        changes = diff_configurations(before, after)
        assert len(changes) == 1
        assert changes[0]["path"] == "ipPermissions[0].ipRanges[0]"
        assert changes[0]["from"] == "10.0.0.0/8"
        assert changes[0]["to"] == "0.0.0.0/0"

    def test_type_change_is_reported_as_modified(self):
        changes = diff_configurations({"v": "1"}, {"v": 1})
        assert changes[0]["change"] == "modified"

    def test_output_is_deterministic_regardless_of_key_order(self):
        before = {"a": 1, "b": 2, "c": 3}
        after = {"c": 3, "b": 99, "a": 1}
        first = diff_configurations(before, after)
        second = diff_configurations({"b": 2, "a": 1, "c": 3}, {"a": 1, "c": 3, "b": 99})
        assert first == second

    def test_multiple_changes_are_sorted_by_path(self):
        changes = diff_configurations({"z": 1, "a": 1}, {"z": 2, "a": 2})
        assert _paths(changes) == ["a", "z"]

    def test_long_values_are_truncated(self):
        long_value = "x" * (_MAX_DIFF_VALUE_CHARS * 3)
        changes = diff_configurations({"policy": "short"}, {"policy": long_value})
        assert len(changes[0]["to"]) < len(long_value)
        assert "chars)" in changes[0]["to"]

    def test_recursion_is_depth_bounded(self):
        before: dict = {"leaf": 1}
        after: dict = {"leaf": 2}
        for _ in range(30):
            before = {"n": before}
            after = {"n": after}
        diff_configurations(before, after)  # must not raise

    def test_none_to_value(self):
        changes = diff_configurations({"x": None}, {"x": "set"})
        assert changes[0]["from"] is None
        assert changes[0]["to"] == "set"


def _config_item(capture_time: datetime, config: dict, status: str = "OK", relationships=None):
    return {
        "resourceType": "AWS::EC2::SecurityGroup",
        "resourceId": "sg-0123456789abcdef0",
        "resourceName": "web-sg",
        "awsRegion": "us-east-1",
        "awsAccountId": "123456789012",
        "configurationItemCaptureTime": capture_time,
        "configurationItemStatus": status,
        "configuration": json.dumps(config),
        "relationships": relationships or [],
    }


@pytest.fixture
def client(monkeypatch):
    c = AWSConfigClient(region="us-east-1")
    fake = MagicMock()
    fake.describe_configuration_recorders.return_value = {
        "ConfigurationRecorders": [{"name": "default"}]
    }
    monkeypatch.setattr(c, "_client", lambda: fake)
    return c, fake


class TestGetConfigurationHistory:
    async def test_first_snapshot_is_a_baseline_not_a_change(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"ipPermissions": []}),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        assert result["count"] == 1
        assert result["modified_count"] == 0
        assert result["changes"][0]["change"] == "baseline"
        assert "no earlier state" in result["changes"][0]["note"]

    async def test_consecutive_snapshots_produce_a_field_diff(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"ipRanges": ["10.0.0.0/8"]}),
                _config_item(datetime(2026, 1, 1, 3, 2, tzinfo=UTC), {"ipRanges": ["0.0.0.0/0"]}),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        assert result["modified_count"] == 1
        change = result["changes"][1]
        assert change["change"] == "modified"
        assert change["changed_fields"][0]["to"] == "0.0.0.0/0"
        assert change["changed_field_count"] == 1

    async def test_every_change_carries_a_citable_evidence_reference(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"a": 1}),
                _config_item(datetime(2026, 1, 1, 3, 5, tzinfo=UTC), {"a": 2}),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        for change in result["changes"]:
            assert change["config_item_id"]
            assert "sg-0123456789abcdef0" in change["config_item_id"]
            assert change["captured_at"]

    async def test_items_are_ordered_chronologically_even_if_api_returns_reversed(self, client):
        # Some regions ignore chronologicalOrder, which would invert every diff.
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 4, 0, tzinfo=UTC), {"v": 2}),
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"v": 1}),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        assert result["changes"][0]["captured_at"] < result["changes"][1]["captured_at"]
        # Direction of travel must be 1 -> 2, not 2 -> 1.
        assert result["changes"][1]["changed_fields"][0]["from"] == 1
        assert result["changes"][1]["changed_fields"][0]["to"] == 2

    async def test_relationships_are_surfaced_for_adjacency(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(
                    datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"a": 1},
                    relationships=[{
                        "resourceType": "AWS::EC2::Instance",
                        "resourceId": "i-0abc",
                        "relationshipName": "Is associated with Instance",
                    }],
                ),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        rel = result["changes"][0]["relationships"][0]
        assert rel["resource_id"] == "i-0abc"
        assert rel["resource_type"] == "AWS::EC2::Instance"

    async def test_recorded_snapshot_with_no_change_is_labelled(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"a": 1}),
                _config_item(datetime(2026, 1, 1, 3, 5, tzinfo=UTC), {"a": 1}),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert result["changes"][1]["change"] == "recorded_no_change"
        assert result["modified_count"] == 0

    async def test_deletion_status_is_preserved(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"a": 1}),
                _config_item(datetime(2026, 1, 1, 3, 5, tzinfo=UTC), {}, status="ResourceDeleted"),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert result["changes"][1]["status"] == "ResourceDeleted"

    async def test_config_not_enabled_is_explained_not_empty(self, client):
        c, fake = client
        fake.describe_configuration_recorders.return_value = {"ConfigurationRecorders": []}
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        assert "not enabled" in result["error"]
        assert result["changes"] == []
        assert "setup_guide" in result

    async def test_undiscovered_resource_is_explained(self, client):
        c, fake = client
        fake.get_resource_config_history.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotDiscoveredException", "Message": "nope"}},
            "GetResourceConfigHistory",
        )
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert "has not recorded" in result["error"]
        assert result["changes"] == []

    async def test_missing_permission_names_the_permission(self, client):
        c, fake = client
        fake.get_resource_config_history.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetResourceConfigHistory",
        )
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert "config:GetResourceConfigHistory" in result["error"]

    async def test_empty_history_is_not_an_error(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {"configurationItems": []}
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")

        assert "error" not in result
        assert result["count"] == 0
        assert "No configuration changes" in result["summary"]

    async def test_window_is_reported(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {"configurationItems": []}
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        result = await c.get_configuration_history(
            "AWS::EC2::SecurityGroup", "sg-0123456789abcdef0",
            start_time=start, end_time=end,
        )
        assert result["window"]["start"] == start.isoformat()
        assert result["window"]["end"] == end.isoformat()

    async def test_limit_is_clamped(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {"configurationItems": []}
        await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0", limit=5000)
        assert fake.get_resource_config_history.call_args.kwargs["limit"] == 100

    async def test_truncation_is_flagged(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [_config_item(datetime(2026, 1, 1, tzinfo=UTC), {"a": 1})],
            "nextToken": "more",
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert result["truncated"] is True

    async def test_secrets_in_configuration_are_redacted(self, client):
        c, fake = client
        fake.get_resource_config_history.return_value = {
            "configurationItems": [
                _config_item(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), {"userData": "safe"}),
                _config_item(
                    datetime(2026, 1, 1, 3, 5, tzinfo=UTC),
                    {"userData": "export AWS_KEY=AKIAIOSFODNN7EXAMPLE"},
                ),
            ]
        }
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        rendered = json.dumps(result)
        assert "AKIAIOSFODNN7EXAMPLE" not in rendered
        assert "REDACTED" in rendered

    async def test_malformed_configuration_json_does_not_crash(self, client):
        c, fake = client
        item = _config_item(datetime(2026, 1, 1, tzinfo=UTC), {"a": 1})
        item["configuration"] = "{not valid json"
        fake.get_resource_config_history.return_value = {"configurationItems": [item]}
        result = await c.get_configuration_history("AWS::EC2::SecurityGroup", "sg-0123456789abcdef0")
        assert result["count"] == 1
