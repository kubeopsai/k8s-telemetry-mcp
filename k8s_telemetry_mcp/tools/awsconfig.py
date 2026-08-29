"""AWS Config client for resource compliance and drift detection."""

import asyncio
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings


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
