"""AWS EC2 client for instance state changes and security group history."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings


class EC2Client:
    """Client for querying EC2 instance state transitions and security group changes.

    EC2 events are the most common infrastructure cause of connectivity failures:
    a security group rule added or removed, an instance stopped/terminated, an AMI
    swap on a launch template. CloudTrail records that the API call happened; this
    client surfaces the current state so the reconstruction engine can reason about
    what the resource looked like at the time of the incident.
    """

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _client(self):
        return boto3.client("ec2", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_instance_state_changes(
        self,
        instance_ids: list[str] | None = None,
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get recent EC2 instance state transitions.

        Uses CloudWatch Events / describe_instances for current state. For historical
        state transitions, pair this with CloudTrail (StartInstances, StopInstances,
        TerminateInstances events).

        Args:
            instance_ids: Specific instance IDs to query. If None, returns all
                          instances modified in the window (via CloudTrail — this
                          call returns current state only).
            timeframe_minutes: Informational — used to scope the summary.
        """
        client = self._client()

        try:
            kwargs: dict[str, Any] = {"MaxResults": 100}
            if instance_ids:
                kwargs = {"InstanceIds": instance_ids[:50]}

            resp = await self._run(client.describe_instances, **kwargs)
        except ClientError as e:
            return {"error": f"EC2 describe_instances failed: {e.response['Error']['Message']}", "instances": []}

        instances = []
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                launch_time = inst.get("LaunchTime")
                name = next(
                    (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"),
                    None,
                )
                instances.append({
                    "instance_id": inst.get("InstanceId"),
                    "name": name,
                    "state": inst.get("State", {}).get("Name"),
                    "state_reason": inst.get("StateReason", {}).get("Message"),
                    "instance_type": inst.get("InstanceType"),
                    "image_id": inst.get("ImageId"),
                    "launch_time": launch_time.isoformat() if launch_time else None,
                    "private_ip": inst.get("PrivateIpAddress"),
                    "public_ip": inst.get("PublicIpAddress"),
                    "subnet_id": inst.get("SubnetId"),
                    "vpc_id": inst.get("VpcId"),
                    "security_groups": [
                        {"group_id": sg.get("GroupId"), "group_name": sg.get("GroupName")}
                        for sg in inst.get("SecurityGroups", [])
                    ],
                })

        return {
            "instances": instances,
            "count": len(instances),
            "timeframe_minutes": timeframe_minutes,
            "note": (
                "This reflects current state. For historical state transitions, "
                "query CloudTrail for StartInstances, StopInstances, TerminateInstances events."
            ),
        }

    async def get_security_group_rules(
        self,
        group_ids: list[str],
    ) -> dict[str, Any]:
        """Get current ingress and egress rules for one or more security groups.

        Pair with get_configuration_history (AWS::EC2::SecurityGroup) to see what
        the rules looked like before a change — this returns the current state.

        Args:
            group_ids: Security group IDs, e.g. ['sg-0abc123'].
        """
        client = self._client()

        try:
            resp = await self._run(
                client.describe_security_groups,
                GroupIds=group_ids[:20],
            )
        except ClientError as e:
            return {"error": f"EC2 describe_security_groups failed: {e.response['Error']['Message']}", "groups": []}

        groups = []
        for sg in resp.get("SecurityGroups", []):
            groups.append({
                "group_id": sg.get("GroupId"),
                "group_name": sg.get("GroupName"),
                "description": sg.get("Description"),
                "vpc_id": sg.get("VpcId"),
                "ingress_rules": [
                    {
                        "protocol": r.get("IpProtocol"),
                        "from_port": r.get("FromPort"),
                        "to_port": r.get("ToPort"),
                        "ip_ranges": [ip.get("CidrIp") for ip in r.get("IpRanges", [])],
                        "ipv6_ranges": [ip.get("CidrIpv6") for ip in r.get("Ipv6Ranges", [])],
                        "source_groups": [g.get("GroupId") for g in r.get("UserIdGroupPairs", [])],
                    }
                    for r in sg.get("IpPermissions", [])
                ],
                "egress_rules": [
                    {
                        "protocol": r.get("IpProtocol"),
                        "from_port": r.get("FromPort"),
                        "to_port": r.get("ToPort"),
                        "ip_ranges": [ip.get("CidrIp") for ip in r.get("IpRanges", [])],
                    }
                    for r in sg.get("IpPermissionsEgress", [])
                ],
            })

        return {"groups": groups, "count": len(groups)}
