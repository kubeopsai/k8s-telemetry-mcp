"""Tests for the exec-host process and its health probe.

The pod's liveness and readiness both depend on this, so the freshness logic needs to
actually be able to fail — the probe it replaces (`importlib.util.find_spec`) reported
success even when the server process was dead.
"""

import time
from pathlib import Path

import pytest

from k8s_telemetry_mcp import keepalive


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("MCP_HEARTBEAT_FILE", str(path))
    return path


class TestHeartbeatFile:
    def test_path_comes_from_environment(self, heartbeat):
        assert keepalive.heartbeat_path() == heartbeat

    def test_default_path_when_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_HEARTBEAT_FILE", raising=False)
        # Compared as Path objects: the container runs Linux, but the test suite also
        # runs on Windows where separators normalise differently.
        assert keepalive.heartbeat_path() == Path(keepalive.DEFAULT_HEARTBEAT_PATH)

    def test_write_then_read_is_fresh(self, heartbeat):
        keepalive.write_heartbeat()
        assert heartbeat.exists()
        assert keepalive.heartbeat_age_seconds() < 5

    def test_age_is_none_when_missing(self, heartbeat):
        assert keepalive.heartbeat_age_seconds() is None

    def test_age_is_none_when_corrupt(self, heartbeat):
        heartbeat.write_text("not-a-timestamp", encoding="utf-8")
        assert keepalive.heartbeat_age_seconds() is None

    def test_age_reflects_written_timestamp(self, heartbeat):
        heartbeat.write_text(str(time.time() - 120), encoding="utf-8")
        assert 115 < keepalive.heartbeat_age_seconds() < 125

    def test_future_timestamp_clamps_to_zero(self, heartbeat):
        heartbeat.write_text(str(time.time() + 500), encoding="utf-8")
        assert keepalive.heartbeat_age_seconds() == 0.0


class TestCheckHeartbeat:
    def test_missing_heartbeat_fails(self, heartbeat):
        assert keepalive.check_heartbeat() is False

    def test_fresh_heartbeat_passes(self, heartbeat):
        keepalive.write_heartbeat()
        assert keepalive.check_heartbeat() is True

    def test_stale_heartbeat_fails(self, heartbeat):
        heartbeat.write_text(str(time.time() - 9999), encoding="utf-8")
        assert keepalive.check_heartbeat() is False

    def test_max_age_boundary_is_respected(self, heartbeat):
        heartbeat.write_text(str(time.time() - 30), encoding="utf-8")
        assert keepalive.check_heartbeat(max_age_seconds=60) is True
        assert keepalive.check_heartbeat(max_age_seconds=10) is False

    def test_default_tolerance_allows_a_few_missed_beats(self):
        # A single slow tick should not restart a healthy pod.
        assert keepalive.DEFAULT_MAX_HEARTBEAT_AGE_SECONDS > keepalive.HEARTBEAT_INTERVAL_SECONDS


class TestProbeExitCodes:
    """`--check` is what the Helm probes invoke, so exit codes are the contract."""

    def test_missing_heartbeat_exits_nonzero(self, heartbeat):
        assert keepalive.main(["--check"]) == 1

    def test_fresh_heartbeat_exits_zero(self, heartbeat):
        keepalive.write_heartbeat()
        assert keepalive.main(["--check"]) == 0

    def test_stale_heartbeat_exits_nonzero(self, heartbeat):
        heartbeat.write_text(str(time.time() - 9999), encoding="utf-8")
        assert keepalive.main(["--check"]) == 1

    def test_custom_max_age_is_honoured(self, heartbeat):
        heartbeat.write_text(str(time.time() - 45), encoding="utf-8")
        assert keepalive.main(["--check", "--max-age", "120"]) == 0
        assert keepalive.main(["--check", "--max-age", "10"]) == 1

    def test_missing_heartbeat_reports_reason(self, heartbeat, capsys):
        keepalive.main(["--check"])
        assert "missing" in capsys.readouterr().err

    def test_stale_heartbeat_reports_age(self, heartbeat, capsys):
        heartbeat.write_text(str(time.time() - 9999), encoding="utf-8")
        keepalive.main(["--check"])
        assert "stale" in capsys.readouterr().err


class TestInstallationValidation:
    def test_reports_version_and_backends(self):
        summary = keepalive._validate_installation()
        assert "ready for exec sessions" in summary
        for role in ("logs", "metrics", "traces"):
            assert role in summary

    def test_import_failure_propagates(self, monkeypatch):
        # A container that cannot import its own server must crash so the failure shows
        # up in pod status rather than at a user's first tool call.
        import k8s_telemetry_mcp.server as server_module

        def boom():
            raise RuntimeError("backend detection exploded")

        monkeypatch.setattr(server_module, "_detect_backends", boom)
        with pytest.raises(RuntimeError, match="exploded"):
            keepalive._validate_installation()
