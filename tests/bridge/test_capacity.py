"""W7 bounded, deterministic host/fake capacity baseline tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.bridge_capacity import CapacityConfig, run


def test_small_fixture_exercises_filter_coalesce_partial_drop_and_recovery():
    result = run(CapacityConfig(registered=4, online=2, active_control=1, telemetry=3, batch_fanout=4, queue_capacity=2, event_log_capacity=8))
    observations = result["observations"]
    # EventLog's current replacement path coalesces the immediately following
    # sample; the next sample creates a fresh retained event.  This assertion
    # locks the observed host behavior without changing bridge/events.py.
    assert observations["event_log"]["coalesced"] == 2
    assert observations["event_log"]["dropped"] > 0
    assert observations["subscription"]["filter"] == "fake-000"
    assert observations["subscription"]["returned"] >= 1
    assert observations["queue"]["dropped"] > 0
    assert result["batch"]["aggregate_state"] == "partial"
    assert result["batch"]["completed"] == 2
    assert result["batch"]["offline"] == 2
    assert observations["db"]["recovered_expired"] == 1


def test_repeat_runs_are_byte_stable_and_unknown_metrics_are_explicit():
    config = CapacityConfig(registered=5, online=3, active_control=2, telemetry=2, batch_fanout=3)
    assert run(config) == run(config)
    observations = run(config)["observations"]
    assert observations["rss_bytes"] == "unknown"
    assert observations["cpu_percent"] == "unknown"
    assert observations["event_loop_lag_ms"] == "unknown"


@pytest.mark.parametrize("kwargs", [
    {"registered": 257},
    {"online": 5, "registered": 4},
    {"active_control": 2, "online": 1},
    {"batch_fanout": 5, "registered": 4},
    {"event_log_capacity": 7},
])
def test_input_limits_and_relationships_are_rejected(kwargs):
    with pytest.raises(ValueError):
        run(CapacityConfig(**kwargs))


def test_cli_emits_json_and_rejects_unbounded_input():
    root = Path(__file__).parents[2]
    command = [sys.executable, str(root / "tools/bridge_capacity.py"), "--registered", "2", "--online", "1", "--batch-fanout", "2"]
    completed = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout)["mode"] == "host_fake_simulation"
    rejected = subprocess.run([*command, "--registered", "257"], cwd=root, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "input limit" in rejected.stderr


def test_zero_population_is_a_valid_bounded_baseline():
    result = run(CapacityConfig(registered=0, online=0, active_control=0, telemetry=0, batch_fanout=0))
    assert result["batch"]["aggregate_state"] == "completed"
    assert result["latency_ms"] == {"basis": "deterministic logical workload units; not wall-clock", "p50": "unknown", "p95": "unknown", "p99": "unknown"}
