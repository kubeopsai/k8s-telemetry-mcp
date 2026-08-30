"""AWS Config client for resource compliance, drift detection, and change history."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings
from k8s_telemetry_mcp.sanitizers import sanitize_structure

# Configuration payloads can be large (a security group with hundreds of rules, an
# IAM policy document). Diffs record values, so cap what is retained per field.
_MAX_DIFF_VALUE_CHARS = 500

# Guard against pathological nesting in a configuration document.
_MAX_DIFF_DEPTH = 8


def _truncate_value(value: Any) -> Any:
    """Shorten a diff value for readability while keeping it recognisable."""
    if isinstance(value, str):
        if len(value) <= _MAX_DIFF_VALUE_CHARS:
            return value
        return value[:_MAX_DIFF_VALUE_CHARS] + f"… (+{len(value) - _MAX_DIFF_VALUE_CHARS} chars)"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    rendered = json.dumps(value, default=str, sort_keys=True)
    if len(rendered) <= _MAX_DIFF_VALUE_CHARS:
        return value
    return rendered[:_MAX_DIFF_VALUE_CHARS] + f"… (+{len(rendered) - _MAX_DIFF_VALUE_CHARS} chars)"


def diff_configurations(
    before: Any,
    after: Any,
    _path: str = "",
    _depth: int = 0,
) -> list[dict[str, Any]]:
    """Compute a deterministic field-level diff between two configuration snapshots.

    Returns a list of ``{"path", "change", "from", "to"}`` records using dotted paths
    with bracketed list indices, e.g. ``ipPermissions[0].fromPort``.

    Deterministic on purpose: the reconstruction engine built on top of this must be
    able to state *what* changed as fact, with the change attributable to a specific
    AWS Config configuration item, rather than relying on a model's description.
    """
    if _depth > _MAX_DIFF_DEPTH:
        return []

    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{_path}.{key}" if _path else key
            if key not in before:
                changes.append({"path": child, "change": "added", "from": None, "to": _truncate_value(after[key])})
            elif key not in after:
                changes.append({"path": child, "change": "removed", "from": _truncate_value(before[key]), "to": None})
            else:
                changes.extend(diff_configurations(before[key], after[key], child, _depth + 1))
        return changes

    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            child = f"{_path}[{index}]"
            if index >= len(before):
                changes.append({"path": child, "change": "added", "from": None, "to": _truncate_value(after[index])})
            elif index >= len(after):
                changes.append({"path": child, "change": "removed", "from": _truncate_value(before[index]), "to": None})
            else:
                changes.extend(diff_configurations(before[index], after[index], child, _depth + 1))
        return changes

    if before != after:
        return [{
            "path": _path or "(root)",
            "change": "modified",
            "from": _truncate_value(before),
            "to": _truncate_value(after),
        }]
    return []


class AWSConfigClient:
    """Client for querying AWS Config compliance and resource history."""

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _client(self):
        return boto3.client("config", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_resource_compliance(
        self,
        resource_id: str | None = None,
        resource_type: str | None = None,
        compliance_filter: str | None = None,
    ) -> dict[str, Any]:
        """Get AWS Config compliance status for resources.

        Args:
            resource_id: Specific resource ID to check (optional)
            resource_type: AWS resource type e.g. 'AWS::EC2::Instance' (optional)
            compliance_filter: Filter by status: COMPLIANT, NON_COMPLIANT, NOT_APPLICABLE (optional)
        """
        client = self._client()

        # First check if AWS Config is enabled
        try:
            recorders = await self._run(client.describe_configuration_recorders)
            if not recorders.get("ConfigurationRecorders"):
                return {
                    "error": "AWS Config is not enabled in this account/region.",
                    "setup_guide": "https://docs.aws.amazon.com/config/latest/developerguide/getting-started.html",
                    "compliance_results": [],
                }
        except ClientError as e:
            return {"error": f"AWS Config unavailable: {e.response['Error']['Message']}", "compliance_results": []}

        try:
            if resource_id:
                # Get compliance for a specific resource
                kwargs: dict[str, Any] = {"ResourceId": resource_id}
                if resource_type:
                    kwargs["ResourceType"] = resource_type
                resp = await self._run(client.get_compliance_details_by_resource, **kwargs)
                results = resp.get("EvaluationResults", [])
                compliance_results = [
                    {
                        "rule_name": r.get("EvaluationResultIdentifier", {})
                            .get("EvaluationResultQualifier", {}).get("ConfigRuleName"),
                        "compliance_type": r.get("ComplianceType"),
                        "result_recorded_time": r.get("ResultRecordedTime").isoformat()
                            if r.get("ResultRecordedTime") else None,
                        "annotation": r.get("Annotation"),
                    }
                    for r in results
                ]
            else:
                # Get compliance summary by rule
                kwargs = {}
                if compliance_filter:
                    kwargs["ComplianceTypes"] = [compliance_filter]
                resp = await self._run(client.describe_compliance_by_config_rule, **kwargs)
                rules = resp.get("ComplianceByConfigRules", [])
                compliance_results = [
                    {
                        "rule_name": r.get("ConfigRuleName"),
                        "compliance_type": r.get("Compliance", {}).get("ComplianceType"),
                        "compliant_count": r.get("Compliance", {})
                            .get("ComplianceContributorCount", {}).get("CappedCount"),
                    }
                    for r in rules
                ]

            non_compliant = [r for r in compliance_results if r.get("compliance_type") == "NON_COMPLIANT"]
            return {
                "resource_id": resource_id,
                "resource_type": resource_type,
                "total_rules_evaluated": len(compliance_results),
                "non_compliant_count": len(non_compliant),
                "compliance_results": compliance_results,
                "summary": "COMPLIANT" if not non_compliant else f"{len(non_compliant)} NON_COMPLIANT rule(s) found",
            }

        except ClientError as e:
            return {"error": f"Compliance query failed: {e.response['Error']['Message']}", "compliance_results": []}

    async def get_configuration_history(
        self,
        resource_type: str,
        resource_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return the configuration-change history for one resource, with field diffs.

        This answers "what actually changed on this resource, and when" — the detail
        CloudTrail cannot give you, because CloudTrail records that an API call happened
        while AWS Config records the resulting state.

        Each returned change carries the configuration item's capture time and a
        `config_item_id` so the change can be cited as evidence. `relationships` gives
        the resources AWS Config considers connected to this one, which is a factual
        adjacency signal rather than an inferred one.

        Args:
            resource_type: AWS Config resource type, e.g. 'AWS::EC2::SecurityGroup'
            resource_id: Resource ID, e.g. 'sg-0123456789abcdef0'
            start_time: Window start (default: 24h before end_time)
            end_time: Window end (default: now)
            limit: Maximum configuration items to retrieve (1-100)
        """
        end_time = end_time or datetime.now(UTC)
        start_time = start_time or (end_time - timedelta(hours=24))
        limit = max(1, min(limit, 100))

        client = self._client()

        try:
            recorders = await self._run(client.describe_configuration_recorders)
            if not recorders.get("ConfigurationRecorders"):
                return {
                    "error": (
                        "AWS Config is not enabled in this account/region, so no "
                        "configuration history exists. Enable it to reconstruct "
                        "infrastructure changes."
                    ),
                    "setup_guide": "https://docs.aws.amazon.com/config/latest/developerguide/getting-started.html",
                    "changes": [],
                }
        except ClientError as e:
            return {"error": f"AWS Config unavailable: {e.response['Error']['Message']}", "changes": []}

        try:
            resp = await self._run(
                client.get_resource_config_history,
                resourceType=resource_type,
                resourceId=resource_id,
                laterTime=end_time,
                earlierTime=start_time,
                chronologicalOrder="Forward",
                limit=limit,
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceNotDiscoveredException":
                return {
                    "error": (
                        f"AWS Config has not recorded {resource_type} {resource_id}. "
                        "Confirm the recorder includes this resource type."
                    ),
                    "changes": [],
                }
            return {
                "error": (
                    f"Configuration history query failed ({code}): "
                    f"{e.response['Error']['Message']}. Requires "
                    "config:GetResourceConfigHistory."
                ),
                "changes": [],
            }

        items = resp.get("configurationItems", [])
        # The API returns newest-first even with chronologicalOrder=Forward in some
        # regions; sort explicitly so diffs are always previous -> next.
        items.sort(key=lambda i: i.get("configurationItemCaptureTime") or datetime.min.replace(tzinfo=UTC))

        changes: list[dict[str, Any]] = []
        previous_config: Any = None
        for index, item in enumerate(items):
            raw = item.get("configuration")
            try:
                current_config = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                current_config = raw

            capture_time = item.get("configurationItemCaptureTime")
            record: dict[str, Any] = {
                "captured_at": capture_time.isoformat() if capture_time else None,
                "status": item.get("configurationItemStatus"),
                # Evidence reference: AWS Config does not expose a single opaque item
                # id, so compose the tuple that uniquely identifies this snapshot.
                "config_item_id": (
                    f"{item.get('resourceType')}/{item.get('resourceId')}"
                    f"@{capture_time.isoformat() if capture_time else 'unknown'}"
                ),
                "resource_name": item.get("resourceName"),
                "aws_region": item.get("awsRegion"),
                "account_id": item.get("awsAccountId"),
                # Factual adjacency, straight from AWS Config.
                "relationships": [
                    {
                        "resource_type": r.get("resourceType"),
                        "resource_id": r.get("resourceId"),
                        "relationship": r.get("relationshipName"),
                    }
                    for r in item.get("relationships", [])
                ],
            }

            if index == 0:
                record["change"] = "baseline"
                record["changed_fields"] = []
                record["note"] = "First snapshot in the window; no earlier state to compare against."
            else:
                field_changes = diff_configurations(previous_config, current_config)
                record["change"] = "modified" if field_changes else "recorded_no_change"
                record["changed_fields"] = field_changes
                record["changed_field_count"] = len(field_changes)

            changes.append(record)
            previous_config = current_config

        modified = [c for c in changes if c.get("change") == "modified"]
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "window": {"start": start_time.isoformat(), "end": end_time.isoformat()},
            "changes": sanitize_structure(changes, settings.enable_sanitization),
            "count": len(changes),
            "modified_count": len(modified),
            "truncated": resp.get("nextToken") is not None,
            "summary": (
                f"{len(modified)} configuration change(s) recorded for {resource_id} in this window"
                if modified
                else f"No configuration changes recorded for {resource_id} in this window"
            ),
        }
