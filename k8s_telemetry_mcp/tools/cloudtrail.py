"""AWS CloudTrail client for audit trail and resource history queries."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.sanitizers import sanitize

logger = logging.getLogger(__name__)


def _bare_resource_name(resource_id: str) -> str:
    """Reduce an ARN to the bare resource name CloudTrail's ResourceName lookup expects.

    CloudTrail indexes resources by their short name, not their ARN. ARN resource parts
    come in several shapes:
        arn:aws:iam::123:role/my-role            -> my-role
        arn:aws:s3:::my-bucket                   -> my-bucket
        arn:aws:lambda:us-east-1:123:function:fn -> fn
        arn:aws:lambda:us-east-1:123:function:fn:3 -> fn   (3 is a version qualifier)
    Non-ARN inputs are returned unchanged.
    """
    if not resource_id.startswith("arn:"):
        return resource_id

    parts = resource_id.split(":", 5)
    resource_part = parts[5] if len(parts) == 6 else resource_id

    if "/" in resource_part:
        candidate = resource_part.split("/")[-1]
    else:
        segments = resource_part.split(":")
        candidate = segments[-1]
        # A trailing all-digit segment is a version/qualifier, not the resource name.
        if len(segments) > 1 and candidate.isdigit():
            candidate = segments[-2]

    return candidate or resource_id


def _sanitize_event(event: dict) -> dict:
    """Sanitize a CloudTrail event, redacting sensitive fields."""
    result = {
        "event_id": event.get("EventId"),
        "event_name": event.get("EventName"),
        "event_time": event.get("EventTime").isoformat() if event.get("EventTime") else None,
        "username": event.get("Username"),
        "resources": [r.get("ResourceName") for r in event.get("Resources", [])],
    }
    raw = event.get("CloudTrailEvent", "{}")
    try:
        ct = json.loads(raw)
        result["source_ip"] = sanitize(str(ct.get("sourceIPAddress", "")))
        result["user_agent"] = sanitize(str(ct.get("userAgent", "")))
        result["aws_region"] = ct.get("awsRegion")
        result["error_code"] = ct.get("errorCode")
        result["error_message"] = sanitize(str(ct.get("errorMessage", ""))) if ct.get("errorMessage") else None
        identity = ct.get("userIdentity", {})
        result["user_identity"] = {
            "type": identity.get("type"),
            "arn": sanitize(str(identity.get("arn", ""))),
            "account_id": identity.get("accountId"),
        }
    except Exception as exc:  # CloudTrail event JSON may be malformed, skip gracefully
        logger.debug("Failed to parse CloudTrail event detail: %s", exc)
        result["detail_parse_error"] = "CloudTrailEvent payload was not valid JSON; only summary fields are available."

    return result


class CloudTrailClient:
    """Client for querying AWS CloudTrail events."""

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _client(self):
        return boto3.client("cloudtrail", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def query_events(
        self,
        keyword: str | None = None,
        event_name: str | None = None,
        username: str | None = None,
        resource_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search CloudTrail events by keyword, event name, username, or resource."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=1))
        limit = max(1, min(limit, 50))

        # CloudTrail LookupEvents accepts exactly ONE LookupAttribute per call, so when
        # several filters are supplied we apply the most specific one and tell the caller
        # which of theirs were dropped. Silently ignoring them produced results that looked
        # filtered but were not.
        candidates = [
            ("event_name", "EventName", event_name),
            ("username", "Username", username),
            ("resource_name", "ResourceName", resource_name),
            ("keyword", "EventName", keyword),
        ]
        supplied = [(name, key, value) for name, key, value in candidates if value]

        lookup_attrs: list[dict[str, str]] = []
        applied_filter: dict[str, str] | None = None
        if supplied:
            name, key, value = supplied[0]
            lookup_attrs = [{"AttributeKey": key, "AttributeValue": value}]
            applied_filter = {"argument": name, "attribute_key": key, "value": value}

        ignored_filters = [name for name, _, _ in supplied[1:]]

        try:
            client = self._client()
            kwargs: dict[str, Any] = {
                "StartTime": start_time,
                "EndTime": end_time,
                "MaxResults": limit,
            }
            if lookup_attrs:
                kwargs["LookupAttributes"] = lookup_attrs

            resp = await self._run(client.lookup_events, **kwargs)
            events = [_sanitize_event(e) for e in resp.get("Events", [])]
            result: dict[str, Any] = {
                "events": events,
                "count": len(events),
                "truncated": resp.get("NextToken") is not None,
                "applied_filter": applied_filter,
                "search_window": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                },
            }
            if ignored_filters:
                result["ignored_filters"] = ignored_filters
                result["filter_note"] = (
                    "CloudTrail supports only one lookup attribute per query. "
                    f"Applied '{applied_filter['argument']}' and ignored {ignored_filters}. "
                    "Issue separate queries to filter on those."
                )
            if applied_filter and applied_filter["argument"] == "keyword":
                result["filter_note"] = (
                    "'keyword' is matched against the CloudTrail EventName as an exact value "
                    "(e.g. 'DeleteBucket'), not as a free-text search."
                )
            return result
        except ClientError as e:
            return {"error": f"CloudTrail query failed: {e.response['Error']['Message']}", "events": []}

    async def get_resource_history(
        self,
        resource_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get full audit trail for a specific resource ID or ARN."""
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(days=90))
        limit = max(1, min(limit, 50))

        resource_name = _bare_resource_name(resource_id)

        try:
            client = self._client()
            resp = await self._run(
                client.lookup_events,
                LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": resource_name}],
                StartTime=start_time,
                EndTime=end_time,
                MaxResults=limit,
            )
            events = [_sanitize_event(e) for e in resp.get("Events", [])]
            return {
                "resource_id": resource_id,
                # Surfaced so the caller can tell when ARN parsing picked the wrong segment
                # and retry with a bare resource name.
                "searched_resource_name": resource_name,
                "search_window_days": (end_time - start_time).days,
                "events": events,
                "count": len(events),
                "truncated": resp.get("NextToken") is not None,
            }
        except ClientError as e:
            return {"error": f"CloudTrail query failed: {e.response['Error']['Message']}", "events": []}
