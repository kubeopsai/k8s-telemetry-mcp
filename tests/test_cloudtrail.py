"""Tests for the CloudTrail client.

This module previously had no test coverage at all, which is how `_sanitize_event`
shipped without a `return` statement — every event in `query_cloudtrail` and
`get_resource_history` came back as `null`. The server tests mock the client, so
they could not catch it.
"""

import json
from datetime import UTC, datetime

import pytest

from k8s_telemetry_mcp.tools.cloudtrail import (
    CloudTrailClient,
    _bare_resource_name,
    _sanitize_event,
)


def _event(**overrides) -> dict:
    detail = {
        "sourceIPAddress": "203.0.113.10",
        "userAgent": "aws-cli/2.15.0",
        "awsRegion": "us-east-1",
        "userIdentity": {
            "type": "IAMUser",
            "arn": "arn:aws:iam::123456789012:user/deploy-bot",
            "accountId": "123456789012",
        },
    }
    detail.update(overrides.pop("detail", {}))
    event = {
        "EventId": "11111111-2222-3333-4444-555555555555",
        "EventName": "DeleteBucket",
        "EventTime": datetime(2026, 1, 1, 3, 14, tzinfo=UTC),
        "Username": "deploy-bot",
        "Resources": [{"ResourceName": "my-bucket", "ResourceType": "AWS::S3::Bucket"}],
        "CloudTrailEvent": json.dumps(detail),
    }
    event.update(overrides)
    return event


class TestSanitizeEvent:
    def test_returns_a_dict_not_none(self):
        assert isinstance(_sanitize_event(_event()), dict)

    def test_summary_fields_are_populated(self):
        result = _sanitize_event(_event())
        assert result["event_id"] == "11111111-2222-3333-4444-555555555555"
        assert result["event_name"] == "DeleteBucket"
        assert result["username"] == "deploy-bot"
        assert result["resources"] == ["my-bucket"]

    def test_event_time_is_iso_formatted(self):
        assert _sanitize_event(_event())["event_time"] == "2026-01-01T03:14:00+00:00"

    def test_missing_event_time_is_none(self):
        assert _sanitize_event(_event(EventTime=None))["event_time"] is None

    def test_detail_fields_are_extracted(self):
        result = _sanitize_event(_event())
        assert result["aws_region"] == "us-east-1"
        assert result["user_identity"]["type"] == "IAMUser"
        assert result["user_identity"]["account_id"] == "123456789012"

    def test_error_code_and_message_surface(self):
        result = _sanitize_event(_event(detail={"errorCode": "AccessDenied", "errorMessage": "nope"}))
        assert result["error_code"] == "AccessDenied"
        assert result["error_message"] == "nope"

    def test_absent_error_message_is_none(self):
        assert _sanitize_event(_event())["error_message"] is None

    def test_malformed_detail_json_still_returns_summary(self):
        result = _sanitize_event(_event(CloudTrailEvent="{not json"))
        assert result["event_name"] == "DeleteBucket"
        assert "detail_parse_error" in result

    def test_empty_resources_gives_empty_list(self):
        assert _sanitize_event(_event(Resources=[]))["resources"] == []

    def test_resource_details_carries_the_type_alongside_the_name(self):
        """Confirmed against a real RevokeSecurityGroupIngress event: CloudTrail returns
        ResourceType and ResourceName together. Discarding the type meant nothing
        downstream could tell a security group id from an RDS instance id without
        already being told, which blocked auto-discovering which resources to deepen
        an AWS Config lookup on."""
        result = _sanitize_event(_event())
        assert result["resource_details"] == [
            {"resource_type": "AWS::S3::Bucket", "resource_name": "my-bucket"}
        ]

    def test_resource_details_is_empty_when_no_resources(self):
        assert _sanitize_event(_event(Resources=[]))["resource_details"] == []

    def test_a_resource_entry_missing_a_name_is_skipped(self):
        """A resource without a name cannot be looked up in Config anyway."""
        result = _sanitize_event(_event(Resources=[{"ResourceType": "AWS::EC2::SecurityGroup"}]))
        assert result["resource_details"] == []

    def test_a_resource_entry_missing_a_type_keeps_the_name_with_a_none_type(self):
        """Some CloudTrail events (older API versions, some services) omit ResourceType.
        The name is still useful; the caller must handle a None type rather than the
        entry vanishing."""
        result = _sanitize_event(_event(Resources=[{"ResourceName": "my-thing"}]))
        assert result["resource_details"] == [{"resource_type": None, "resource_name": "my-thing"}]

    def test_secrets_in_detail_are_redacted(self):
        result = _sanitize_event(_event(detail={"userAgent": "curl password=hunter2"}))
        assert "hunter2" not in result["user_agent"]


class TestBareResourceName:
    @pytest.mark.parametrize(
        ("resource_id", "expected"),
        [
            ("arn:aws:iam::123456789012:role/my-role", "my-role"),
            ("arn:aws:iam::123456789012:role/path/to/my-role", "my-role"),
            ("arn:aws:s3:::my-bucket", "my-bucket"),
            ("arn:aws:lambda:us-east-1:123456789012:function:my-fn", "my-fn"),
            # A trailing numeric segment is a version qualifier, not the name.
            ("arn:aws:lambda:us-east-1:123456789012:function:my-fn:3", "my-fn"),
            ("arn:aws:sqs:us-east-1:123456789012:my-queue", "my-queue"),
            # Non-ARN input passes through untouched.
            ("i-0abc123def456", "i-0abc123def456"),
            ("my-bucket", "my-bucket"),
        ],
    )
    def test_extraction(self, resource_id, expected):
        assert _bare_resource_name(resource_id) == expected


class _FakeCloudTrail:
    """Records the kwargs it was called with and returns a canned page."""

    def __init__(self, events=None, next_token=None):
        self._events = events if events is not None else [_event()]
        self._next_token = next_token
        self.calls: list[dict] = []

    def lookup_events(self, **kwargs):
        self.calls.append(kwargs)
        resp = {"Events": self._events}
        if self._next_token:
            resp["NextToken"] = self._next_token
        return resp


@pytest.fixture
def client(monkeypatch):
    ct = CloudTrailClient(region="us-east-1")
    fake = _FakeCloudTrail()
    monkeypatch.setattr(ct, "_client", lambda: fake)
    return ct, fake


class TestQueryEvents:
    async def test_events_are_dicts_not_nulls(self, client):
        ct, _ = client
        result = await ct.query_events(event_name="DeleteBucket")
        assert result["count"] == 1
        assert all(isinstance(e, dict) for e in result["events"])
        assert result["events"][0]["event_name"] == "DeleteBucket"

    async def test_event_name_becomes_a_lookup_attribute(self, client):
        ct, fake = client
        await ct.query_events(event_name="DeleteBucket")
        assert fake.calls[0]["LookupAttributes"] == [
            {"AttributeKey": "EventName", "AttributeValue": "DeleteBucket"}
        ]

    async def test_username_filter_is_applied_when_alone(self, client):
        ct, fake = client
        await ct.query_events(username="deploy-bot")
        assert fake.calls[0]["LookupAttributes"][0]["AttributeKey"] == "Username"

    async def test_no_filters_omits_lookup_attributes(self, client):
        ct, fake = client
        result = await ct.query_events()
        assert "LookupAttributes" not in fake.calls[0]
        assert result["applied_filter"] is None

    async def test_conflicting_filters_are_reported_not_silently_dropped(self, client):
        # CloudTrail accepts one lookup attribute per call. Previously the extra
        # arguments vanished and the caller believed they had been applied.
        ct, _ = client
        result = await ct.query_events(event_name="DeleteBucket", username="deploy-bot")
        assert result["applied_filter"]["argument"] == "event_name"
        assert result["ignored_filters"] == ["username"]
        assert "only one lookup attribute" in result["filter_note"]

    async def test_keyword_semantics_are_disclosed(self, client):
        ct, _ = client
        result = await ct.query_events(keyword="DeleteBucket")
        assert "exact value" in result["filter_note"]

    async def test_limit_is_clamped_to_fifty(self, client):
        ct, fake = client
        await ct.query_events(limit=5000)
        assert fake.calls[0]["MaxResults"] == 50

    async def test_limit_floor_is_one(self, client):
        ct, fake = client
        await ct.query_events(limit=0)
        assert fake.calls[0]["MaxResults"] == 1

    async def test_search_window_is_returned(self, client):
        ct, _ = client
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        result = await ct.query_events(start_time=start, end_time=end)
        assert result["search_window"]["start"] == start.isoformat()
        assert result["search_window"]["end"] == end.isoformat()

    async def test_truncation_is_flagged(self, monkeypatch):
        ct = CloudTrailClient()
        monkeypatch.setattr(ct, "_client", lambda: _FakeCloudTrail(next_token="more"))
        assert (await ct.query_events())["truncated"] is True

    async def test_empty_result_set(self, monkeypatch):
        ct = CloudTrailClient()
        monkeypatch.setattr(ct, "_client", lambda: _FakeCloudTrail(events=[]))
        result = await ct.query_events()
        assert result["events"] == []
        assert result["count"] == 0


class TestGetResourceHistory:
    async def test_events_are_dicts_not_nulls(self, client):
        ct, _ = client
        result = await ct.get_resource_history("arn:aws:s3:::my-bucket")
        assert result["count"] == 1
        assert result["events"][0]["event_name"] == "DeleteBucket"

    async def test_arn_is_reduced_before_lookup(self, client):
        ct, fake = client
        await ct.get_resource_history("arn:aws:iam::123456789012:role/my-role")
        assert fake.calls[0]["LookupAttributes"] == [
            {"AttributeKey": "ResourceName", "AttributeValue": "my-role"}
        ]

    async def test_searched_name_is_surfaced_for_debugging(self, client):
        ct, _ = client
        result = await ct.get_resource_history("arn:aws:lambda:us-east-1:1:function:fn:7")
        assert result["searched_resource_name"] == "fn"
        assert result["resource_id"] == "arn:aws:lambda:us-east-1:1:function:fn:7"

    async def test_window_days_reported(self, client):
        ct, _ = client
        result = await ct.get_resource_history(
            "my-bucket",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 31, tzinfo=UTC),
        )
        assert result["search_window_days"] == 30
