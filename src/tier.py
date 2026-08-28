"""Community edition limits.

The community edition is permanently locked to a single Kubernetes namespace
and a subset of tools. These limits are enforced in code and cannot be
overridden via environment variables.

To unlock unlimited namespaces, analytics, AWS tools, and advanced Kubernetes
tools, subscribe to the Professional or Enterprise tier on AWS Marketplace:
https://aws.amazon.com/marketplace/pp/prodview-YOUR_PRODUCT_ID

Tier comparison:
  Community (free):    1 namespace, 100 log lines, 7 core tools
  Professional ($99):  unlimited namespaces, 500 log lines, 20 tools
  Enterprise ($299):   unlimited namespaces, 5000 log lines, all 22 tools, 72h range
"""

import logging

logger = logging.getLogger("k8s-telemetry-mcp")

# Hard limits — not configurable
MAX_NAMESPACES = 1
MAX_LOG_LINES = 100
MAX_QUERY_RANGE_HOURS = 24

# Tools available in the community edition
COMMUNITY_TOOLS = frozenset({
    "query_pod_logs",
    "query_logs_custom",
    "get_pod_metrics",
    "get_cluster_health",
    "get_trace",
    "search_traces",
    "get_k8s_events",
})

_UPGRADE_URL = "https://aws.amazon.com/marketplace"


class TierViolationError(ValueError):
    """Raised when a request exceeds community edition limits."""


class CommunityEnforcer:
    """Enforces community edition limits. Stateful — tracks namespace usage."""

    def __init__(self) -> None:
        self._seen_namespaces: set[str] = set()
        logger.info(
            "Community edition enforcer initialized: "
            f"max_namespaces={MAX_NAMESPACES}, max_log_lines={MAX_LOG_LINES}"
        )

    @property
    def max_log_lines(self) -> int:
        return MAX_LOG_LINES

    @property
    def max_query_range_hours(self) -> int:
        return MAX_QUERY_RANGE_HOURS

    def check_tool(self, tool_name: str) -> None:
        """Raise TierViolationError if tool is not in the community edition."""
        if tool_name not in COMMUNITY_TOOLS:
            raise TierViolationError(
                f"'{tool_name}' is not available in the Community Edition. "
                f"Upgrade to Professional ($99/mo) or Enterprise ($299/mo) to unlock "
                f"analytics, AWS tools, and advanced Kubernetes tools. "
                f"Subscribe at: {_UPGRADE_URL}"
            )

    def check_namespace(self, namespace: str) -> None:
        """Raise TierViolationError if namespace limit is exceeded."""
        if namespace in self._seen_namespaces:
            return

        if len(self._seen_namespaces) >= MAX_NAMESPACES:
            raise TierViolationError(
                f"The Community Edition is limited to {MAX_NAMESPACES} Kubernetes namespace. "
                f"You are already monitoring: {sorted(self._seen_namespaces)}. "
                f"Upgrade to Professional ($99/mo) for unlimited namespace access. "
                f"Subscribe at: {_UPGRADE_URL}"
            )

        self._seen_namespaces.add(namespace)
        logger.debug(f"Namespace '{namespace}' approved ({len(self._seen_namespaces)}/{MAX_NAMESPACES})")
