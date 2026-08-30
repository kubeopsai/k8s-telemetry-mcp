"""Configuration for K8s Telemetry MCP Server."""

from pydantic_settings import BaseSettings

VERSION = "1.2.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Loki Configuration (optional — omit if using Datadog or CloudWatch)
    loki_url: str = ""
    loki_timeout: int = 30

    # Prometheus Configuration (optional — omit if using Datadog or CloudWatch)
    prometheus_url: str = ""
    prometheus_timeout: int = 30

    # Tempo Configuration (optional)
    tempo_url: str = ""
    tempo_timeout: int = 30

    # Datadog Configuration (optional — set API/app keys to enable)
    datadog_api_key: str = ""
    datadog_app_key: str = ""
    datadog_site: str = "datadoghq.com"

    # CloudWatch Configuration (optional — set log group to enable)
    cloudwatch_log_group: str = ""
    cloudwatch_region: str = ""

    # Alertmanager Configuration (optional)
    alertmanager_url: str = ""

    # AWS region for CloudTrail, Config, ECR, RDS, ElastiCache tools
    aws_region: str = "us-east-1"

    # Security Settings
    enable_sanitization: bool = True
    max_log_lines: int = 500
    max_query_range_hours: int = 24

    # Server Settings
    server_name: str = "k8s-telemetry-mcp"
    server_version: str = VERSION
    log_level: str = "INFO"

    model_config = {"env_prefix": "MCP_"}


settings = Settings()
