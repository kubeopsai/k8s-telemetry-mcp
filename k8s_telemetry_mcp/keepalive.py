"""Exec-host process for in-cluster deployments.

The MCP server speaks over stdio. AI assistants reach it with

    kubectl exec -i -n monitoring deploy/k8s-telemetry-mcp -- k8s-telemetry-mcp

which starts a *new* process whose stdin is the assistant's. The pod's own main
process therefore must not be the stdio server: with no stdin attached it reads EOF
immediately and exits, which in a Deployment means CrashLoopBackOff.

This module is that main process. It does three useful things beyond staying alive:

* Imports the server module at startup, so a broken image or invalid configuration
  fails loudly and visibly instead of lurking until someone's first tool call.
* Logs which telemetry backends were detected, making misconfiguration obvious in
  ``kubectl logs``.
* Maintains a heartbeat file so liveness and readiness probes can assert something
  real. The previous probes ran ``importlib.util.find_spec(...)``, which reports
  whether a module is installed — true even when the process is dead.

Run ``k8s-telemetry-mcp-host --check`` to evaluate heartbeat freshness; that is the
form the probes use.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Lives on the pod-local /tmp emptyDir, which is writable even with
# readOnlyRootFilesystem: true.
DEFAULT_HEARTBEAT_PATH = "/tmp/k8s-telemetry-mcp-heartbeat"
HEARTBEAT_INTERVAL_SECONDS = 15
# Three missed beats before a probe fails, so a single slow scheduling tick does not
# restart an otherwise healthy pod.
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 4

logger = logging.getLogger("k8s-telemetry-mcp.host")


def heartbeat_path() -> Path:
    return Path(os.environ.get("MCP_HEARTBEAT_FILE", DEFAULT_HEARTBEAT_PATH))


def write_heartbeat(path: Path | None = None) -> None:
    """Record the current monotonic-independent wall clock time."""
    target = path or heartbeat_path()
    target.write_text(str(time.time()), encoding="utf-8")


def heartbeat_age_seconds(path: Path | None = None) -> float | None:
    """Age of the heartbeat in seconds, or None if it is missing or unreadable."""
    target = path or heartbeat_path()
    try:
        written_at = float(target.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, time.time() - written_at)


def check_heartbeat(
    max_age_seconds: float = DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
    path: Path | None = None,
) -> bool:
    """True when a heartbeat exists and is fresh enough."""
    age = heartbeat_age_seconds(path)
    return age is not None and age <= max_age_seconds


def _validate_installation() -> str:
    """Import the server module and return a human-readable backend summary.

    Raised exceptions are intentionally allowed to propagate: a container that cannot
    import its own server should crash so the failure is visible in pod status.
    """
    from k8s_telemetry_mcp import server
    from k8s_telemetry_mcp.config import settings

    log_client, metrics_client, trace_client = server._detect_backends()
    detected = {
        "logs": type(log_client).__name__ if log_client else "none",
        "metrics": type(metrics_client).__name__ if metrics_client else "none",
        "traces": type(trace_client).__name__ if trace_client else "none",
    }
    return (
        f"v{settings.server_version} ready for exec sessions — "
        + ", ".join(f"{role}={name}" for role, name in detected.items())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="k8s-telemetry-mcp-host",
        description="Long-lived host process for exec-based MCP sessions.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 if the heartbeat is fresh, 1 otherwise. Used by liveness/readiness probes.",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
        help="Maximum acceptable heartbeat age in seconds (with --check).",
    )
    args = parser.parse_args(argv)

    if args.check:
        if check_heartbeat(args.max_age):
            return 0
        age = heartbeat_age_seconds()
        print(
            "heartbeat missing" if age is None else f"heartbeat stale ({age:.0f}s old)",
            file=sys.stderr,
        )
        return 1

    logging.basicConfig(
        level=os.environ.get("MCP_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(_validate_installation())
    logger.info(
        "Connect an assistant with: kubectl exec -i -n <namespace> "
        "deploy/k8s-telemetry-mcp -- k8s-telemetry-mcp"
    )

    while True:
        write_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
