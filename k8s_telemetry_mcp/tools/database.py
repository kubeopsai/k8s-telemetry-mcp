"""AWS RDS + ElastiCache client for database performance insights."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from k8s_telemetry_mcp.config import settings


class DatabaseInsightsClient:
    """Client for querying RDS Performance Insights and CloudWatch database metrics."""

    def __init__(self, region: str | None = None):
        self._region = region or settings.aws_region or None

    def _pi_client(self):
        return boto3.client("pi", region_name=self._region)

    def _rds_client(self):
        return boto3.client("rds", region_name=self._region)

    def _elasticache_client(self):
        return boto3.client("elasticache", region_name=self._region)

    def _cw_client(self):
        return boto3.client("cloudwatch", region_name=self._region)

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_database_insights(
        self,
        db_identifier: str,
        db_type: str = "rds",
        timeframe_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get database performance insights.

        Tries RDS Performance Insights first (rich slow query data).
        Falls back to CloudWatch basic metrics if Performance Insights is not enabled.

        Args:
            db_identifier: RDS instance/cluster ID or ElastiCache cluster ID
            db_type: 'rds' or 'elasticache'
            timeframe_minutes: Time window to analyze
        """
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=timeframe_minutes)

        if db_type == "elasticache":
            return await self._elasticache_metrics(db_identifier, start_time, end_time)

        # RDS: try Performance Insights first
        return await self._rds_insights(db_identifier, start_time, end_time, timeframe_minutes)

    async def _rds_insights(
        self, db_identifier: str, start_time: datetime, end_time: datetime, timeframe_minutes: int
    ) -> dict[str, Any]:
        """Query RDS Performance Insights, fall back to CloudWatch if unavailable."""
        # First get the DbiResourceId needed for PI API
        try:
            rds = self._rds_client()
            resp = await self._run(rds.describe_db_instances, DBInstanceIdentifier=db_identifier)
            instances = resp.get("DBInstances", [])
            if not instances:
                return {"error": f"RDS instance '{db_identifier}' not found.", "metrics": {}}
            instance = instances[0]
            dbi_resource_id = instance.get("DbiResourceId")
            engine = instance.get("Engine", "unknown")
            pi_enabled = instance.get("PerformanceInsightsEnabled", False)
        except ClientError as e:
            return {"error": f"RDS describe failed: {e.response['Error']['Message']}", "metrics": {}}

        if pi_enabled and dbi_resource_id:
            try:
                return await self._performance_insights(
                    db_identifier, dbi_resource_id, engine, start_time, end_time
                )
            except ClientError:
                pass  # Fall through to CloudWatch

        # Fall back to CloudWatch basic metrics
        return await self._rds_cloudwatch_fallback(db_identifier, engine, start_time, end_time, timeframe_minutes)

    async def _performance_insights(
        self,
        db_identifier: str,
        dbi_resource_id: str,
        engine: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        """Query RDS Performance Insights for top SQL and wait events."""
        pi = self._pi_client()
        period_seconds = max(60, int((end_time - start_time).total_seconds() / 60))

        # Top SQL by load
        sql_resp = await self._run(
            pi.get_resource_metrics,
            ServiceType="RDS",
            Identifier=dbi_resource_id,
            MetricQueries=[
                {
                    "Metric": "db.load.avg",
                    "GroupBy": {"Group": "db.sql", "Limit": 10},
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            PeriodInSeconds=period_seconds,
        )

        top_sql = []
        for mr in sql_resp.get("MetricList", []):
            for key in mr.get("Keys", []):
                dimensions = key.get("Dimensions", {})
                top_sql.append({
                    "sql_text": dimensions.get("db.sql.statement", "")[:500],
                    "db_load_avg": key.get("Total"),
                })

        return {
            "db_identifier": db_identifier,
            "engine": engine,
            "data_source": "performance_insights",
            "time_window_minutes": int((end_time - start_time).total_seconds() / 60),
            "top_sql_by_load": top_sql[:10],
        }

    async def _rds_cloudwatch_fallback(
        self,
        db_identifier: str,
        engine: str,
        start_time: datetime,
        end_time: datetime,
        timeframe_minutes: int,
    ) -> dict[str, Any]:
        """Fall back to CloudWatch RDS metrics when Performance Insights is disabled."""
        warning = (
            "RDS Performance Insights is not enabled for this instance. "
            "Returning basic CloudWatch metrics only. "
            "For slow query analysis and top SQL data, enable Performance Insights on your RDS instance: "
            "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.Enabling.html"
        )
        cw = self._cw_client()
        period = max(60, timeframe_minutes * 60 // 20)

        metric_names = ["DatabaseConnections", "ReadLatency", "WriteLatency",
                        "CPUUtilization", "FreeableMemory", "ReadIOPS", "WriteIOPS"]
        metrics: dict[str, Any] = {}
        for metric_name in metric_names:
            try:
                resp = await self._run(
                    cw.get_metric_statistics,
                    Namespace="AWS/RDS",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_identifier}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=period,
                    Statistics=["Average", "Maximum"],
                )
                dps = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
                if dps:
                    metrics[metric_name] = {
                        "average": round(dps[-1]["Average"], 4),
                        "maximum": round(dps[-1]["Maximum"], 4),
                    }
            except ClientError:
                pass

        return {
            "warning": warning,
            "db_identifier": db_identifier,
            "engine": engine,
            "data_source": "cloudwatch_basic",
            "time_window_minutes": timeframe_minutes,
            "metrics": metrics,
        }

    async def _elasticache_metrics(
        self, cluster_id: str, start_time: datetime, end_time: datetime
    ) -> dict[str, Any]:
        """Query ElastiCache CloudWatch metrics."""
        timeframe_minutes = int((end_time - start_time).total_seconds() / 60)
        cw = self._cw_client()
        period = max(60, timeframe_minutes * 60 // 20)

        metric_names = ["CurrConnections", "CacheHits", "CacheMisses",
                        "CurrItems", "BytesUsedForCache", "CPUUtilization",
                        "GetTypeCmds", "SetTypeCmds", "Evictions"]
        metrics: dict[str, Any] = {}
        for metric_name in metric_names:
            try:
                resp = await self._run(
                    cw.get_metric_statistics,
                    Namespace="AWS/ElastiCache",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "CacheClusterId", "Value": cluster_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=period,
                    Statistics=["Average", "Sum"],
                )
                dps = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
                if dps:
                    metrics[metric_name] = {
                        "average": round(dps[-1]["Average"], 4),
                        "sum": round(dps[-1]["Sum"], 4),
                    }
            except ClientError:
                pass

        hit_rate = None
        if "CacheHits" in metrics and "CacheMisses" in metrics:
            hits = metrics["CacheHits"]["sum"]
            misses = metrics["CacheMisses"]["sum"]
            total = hits + misses
            hit_rate = round(hits / total, 4) if total > 0 else None

        return {
            "cluster_id": cluster_id,
            "db_type": "elasticache",
            "data_source": "cloudwatch",
            "time_window_minutes": timeframe_minutes,
            "cache_hit_rate": hit_rate,
            "metrics": metrics,
        }
