"""AWS CloudTrail client for audit trail and resource history queries."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from src.config import settings
from src.sanitizers import sanitize

logger = logging.getLogger(__name__)


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

        lookup_attrs = []
        if event_name:
            lookup_attrs.append({"AttributeKey": "EventName", "AttributeValue": event_name})
        elif username:
            lookup_attrs.append({"AttributeKey": "Username", "AttributeValue": username})
        elif resource_name:
            lookup_attrs.append({"AttributeKey": "ResourceName", "AttributeValue": resource_name})
        elif keyword:
            lookup_attrs.append({"AttributeKey": "EventName", "AttributeValue": keyword})

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
            return {"events": events, "count": len(events), "truncated": resp.get("NextToken") is not None}
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

        # Strip ARN to bare resource name for lookup
        resource_name = resource_id.split("/")[-1].split(":")[-1]

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
                "search_window_days": (end_time - start_time).days,
                "events": events,
                "count": len(events),
                "truncated": resp.get("NextToken") is not None,
            }
        except ClientError as e:
            return {"error": f"CloudTrail query failed: {e.response['Error']['Message']}", "events": []}
