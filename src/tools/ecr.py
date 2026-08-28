"""AWS ECR + Inspector client for container image vulnerability scanning."""

import asyncio
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from src.config import settings


class ECRClient:
    """Client for querying ECR image vulnerabilities via Inspector v2."""

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _ecr_client(self):
        return boto3.client("ecr", region_name=self._region)

    def _inspector_client(self):
        return boto3.client("inspector2", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_image_vulnerabilities(
        self,
        repository_name: str,
        image_tag: str = "latest",
        severity_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get vulnerability findings for a container image.

        Tries Inspector v2 first (richer CVE data), falls back to ECR basic
        scan results if Inspector is not enabled.

        Args:
            repository_name: ECR repository name
            image_tag: Image tag to scan (default: latest)
            severity_filter: Filter by severity levels e.g. ['CRITICAL', 'HIGH']
        """
        severity_filter = severity_filter or ["CRITICAL", "HIGH", "MEDIUM"]

        # Try Inspector v2 first
        try:
            inspector = self._inspector_client()
            filter_criteria: dict[str, Any] = {
                "ecrImageRepositoryName": [{"comparison": "EQUALS", "value": repository_name}],
                "ecrImageTags": [{"comparison": "EQUALS", "value": image_tag}],
            }
            if severity_filter:
                filter_criteria["severity"] = [
                    {"comparison": "EQUALS", "value": s} for s in severity_filter
                ]

            resp = await self._run(
                inspector.list_findings,
                filterCriteria=filter_criteria,
                maxResults=100,
            )
            findings = resp.get("findings", [])
            return self._format_inspector_findings(repository_name, image_tag, findings)

        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException"):
                # Inspector not enabled — fall back to ECR basic scan
                return await self._ecr_basic_scan_fallback(repository_name, image_tag, severity_filter, reason=code)
            return {"error": f"Inspector query failed: {e.response['Error']['Message']}", "findings": []}

    def _format_inspector_findings(
        self, repository_name: str, image_tag: str, findings: list
    ) -> dict[str, Any]:
        severity_counts: dict[str, int] = {}
        formatted = []
        for f in findings:
            sev = f.get("severity", "UNKNOWN")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            vuln = f.get("packageVulnerabilityDetails", {})
            formatted.append({
                "finding_arn": f.get("findingArn"),
                "severity": sev,
                "title": f.get("title"),
                "description": f.get("description", "")[:300],
                "cve_id": vuln.get("vulnerabilityId"),
                "cvss_score": vuln.get("cvss", [{}])[0].get("baseScore") if vuln.get("cvss") else None,
                "fixed_in_version": vuln.get("fixedInVersions", [None])[0] if vuln.get("fixedInVersions") else None,
                "package_name": vuln.get("vulnerablePackages", [{}])[0].get("name") if vuln.get("vulnerablePackages") else None,
            })
        return {
            "repository": repository_name,
            "image_tag": image_tag,
            "scan_source": "inspector_v2",
            "total_findings": len(formatted),
            "severity_counts": severity_counts,
            "findings": formatted,
        }

    async def _ecr_basic_scan_fallback(
        self, repository_name: str, image_tag: str, severity_filter: list[str], reason: str
    ) -> dict[str, Any]:
        """Fall back to ECR basic scan results when Inspector v2 is unavailable."""
        warning = (
            f"AWS Inspector v2 is not available ({reason}). "
            "Falling back to ECR basic scan results, which provide less detail. "
            "To enable richer vulnerability data, activate Inspector v2 in your AWS account: "
            "https://docs.aws.amazon.com/inspector/latest/user/getting_started_tutorial.html"
        )
        try:
            ecr = self._ecr_client()
            resp = await self._run(
                ecr.describe_image_scan_findings,
                repositoryName=repository_name,
                imageId={"imageTag": image_tag},
            )
            scan_status = resp.get("imageScanStatus", {}).get("status")
            if scan_status != "COMPLETE":
                return {
                    "warning": warning,
                    "repository": repository_name,
                    "image_tag": image_tag,
                    "scan_source": "ecr_basic",
                    "scan_status": scan_status,
                    "message": "ECR basic scan not complete. Enable scan-on-push in your ECR repository settings.",
                    "findings": [],
                }

            findings_data = resp.get("imageScanFindings", {})
            counts = findings_data.get("findingSeverityCounts", {})
            findings = [
                {
                    "severity": f.get("severity"),
                    "name": f.get("name"),
                    "description": f.get("description", "")[:300],
                    "uri": f.get("uri"),
                }
                for f in findings_data.get("findings", [])
                if not severity_filter or f.get("severity") in severity_filter
            ]
            return {
                "warning": warning,
                "repository": repository_name,
                "image_tag": image_tag,
                "scan_source": "ecr_basic",
                "scan_status": "COMPLETE",
                "total_findings": len(findings),
                "severity_counts": counts,
                "findings": findings,
            }
        except ClientError as e:
            return {
                "warning": warning,
                "error": f"ECR basic scan also unavailable: {e.response['Error']['Message']}",
                "findings": [],
            }
