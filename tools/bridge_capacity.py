#!/usr/bin/env python3
"""Deterministic, host-only W7 Web Bridge capacity baseline runner.

This is a bounded logical simulation.  It does not connect to a device or the
network and its synthetic latency numbers are workload units, not wall-clock
capacity evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from typing import Any

from bridge.domain import BatchState, BatchTarget, BatchTask
from bridge.events import EventLog
from bridge.persistence import SQLiteStore


MAX_INPUT = 256
MAX_EVENT_CAPACITY = 256
MAX_WORK = 1_000_000


@dataclass(frozen=True)
class CapacityConfig:
    registered: int = 4
    online: int = 3
    active_control: int = 1
    telemetry: int = 4
    batch_fanout: int = 4
    queue_capacity: int = 32
    event_log_capacity: int = 32
    seed: int = 0

    def validate(self) -> None:
        fields = {
            "registered": self.registered,
            "online": self.online,
            "active_control": self.active_control,
            "telemetry": self.telemetry,
            "batch_fanout": self.batch_fanout,
            "queue_capacity": self.queue_capacity,
            "event_log_capacity": self.event_log_capacity,
        }
        for name, value in fields.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            if value > MAX_INPUT:
                raise ValueError(f"{name} exceeds input limit {MAX_INPUT}")
        if self.event_log_capacity < 8:
            raise ValueError("event_log_capacity must be at least 8")
        if self.queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if self.online > self.registered:
            raise ValueError("online cannot exceed registered")
        if self.active_control > self.online:
            raise ValueError("active_control cannot exceed online")
        if self.batch_fanout > self.registered:
            raise ValueError("batch_fanout cannot exceed registered")
        work = self.registered + self.online * max(1, self.telemetry) + self.event_log_capacity
        if work > MAX_WORK:
            raise ValueError("configuration exceeds bounded logical work limit")


def _quantiles(values: list[int]) -> dict[str, int | str]:
    if not values:
        return {"p50": "unknown", "p95": "unknown", "p99": "unknown"}
    ordered = sorted(values)

    def percentile(percent: int) -> int:
        return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]

    return {"p50": percentile(50), "p95": percentile(95), "p99": percentile(99)}


def run(config: CapacityConfig) -> dict[str, Any]:
    """Run a repeatable simulation and return JSON-serialisable observations."""

    config.validate()
    devices = [f"fake-{index:03d}" for index in range(config.registered)]
    online = set(devices[: config.online])
    active = set(devices[: config.active_control])
    log = EventLog(max_events=config.event_log_capacity)
    coalesced = 0
    event_drops = 0
    telemetry_attempts = 0
    previous_ids: dict[str, str] = {}
    logical_latencies: list[int] = []

    # Critical events are intentionally emitted before the final telemetry
    # burst, exercising EventLog's rule that telemetry cannot evict critical
    # events once its bounded window has no telemetry left to evict.
    for device_id in sorted(active):
        log.append(type="command.state.changed", device_id=device_id, payload={"state": "executing"})
        logical_latencies.append(4 + (int(device_id[-3:]) % 3))
    for device_id in sorted(online):
        for sample in range(config.telemetry):
            telemetry_attempts += 1
            event = log.append(
                type="telemetry.sampled",
                device_id=device_id,
                telemetry_key=device_id,
                payload={"sample": sample, "device": device_id},
            )
            if event.cursor == 0:
                event_drops += 1
            elif previous_ids.get(device_id) == event.event_id:
                coalesced += 1
            previous_ids[device_id] = event.event_id
            logical_latencies.append(1 + sample + (int(device_id[-3:]) % 2))
    # Fill the retained window with non-telemetry events, then retry one sample
    # per online device.  This makes event-log drops observable and bounded.
    for index in range(config.event_log_capacity):
        device_id = devices[index % config.registered] if devices else None
        log.append(type="safety.changed", device_id=device_id, payload={"n": index})
    for device_id in sorted(online):
        event = log.append(
            type="telemetry.sampled", device_id=device_id, telemetry_key=f"refresh-{device_id}", payload={"refresh": True}
        )
        telemetry_attempts += 1
        if event.cursor == 0:
            event_drops += 1

    queue_offered = config.active_control + telemetry_attempts + config.batch_fanout
    queue_dropped = max(0, queue_offered - config.queue_capacity)
    selected_device = devices[0] if devices else None
    all_events = log.since(0, limit=500)
    filtered_events = log.since(0, device_ids=[selected_device] if selected_device else [], include_telemetry=False)

    completed = min(config.batch_fanout, config.online)
    offline = config.batch_fanout - completed
    aggregate = (
        BatchState.COMPLETED.value if offline == 0 else
        BatchState.PARTIAL.value if completed else BatchState.FAILED.value
    )

    # Use the real persistence recovery operation, but only an in-memory DB and
    # deterministic records.  SQLite timing is deliberately not reported.
    store = SQLiteStore()
    task = BatchTask(
        task_id="task-simulation", command_type="control.status", params={},
        targets=[BatchTarget(device_id=device_id) for device_id in devices[: config.batch_fanout]],
        aggregate_state=BatchState.RUNNING,
    )
    store.save_batch(task)
    recovered = store.recover_inflight_batches()
    store.close()

    return {
        "schema": "lifeos.bridge.capacity.v1",
        "mode": "host_fake_simulation",
        "claim": "capacity_method_baseline_only",
        "config": config.__dict__,
        "population": {"registered": config.registered, "online": config.online, "active_control": config.active_control},
        "latency_ms": {"basis": "deterministic logical workload units; not wall-clock", **_quantiles(logical_latencies)},
        "observations": {
            "rss_bytes": "unknown",
            "cpu_percent": "unknown",
            "event_loop_lag_ms": "unknown",
            "db_latency_ms": "unknown",
            "event_log": {"retained": len(all_events), "filtered_non_telemetry": len(filtered_events), "telemetry_attempts": telemetry_attempts, "dropped": event_drops, "coalesced": coalesced},
            "subscription": {"filter": selected_device, "returned": len(filtered_events), "all_retained_returned": len(all_events)},
            "queue": {"offered": queue_offered, "peak": min(queue_offered, config.queue_capacity), "capacity": config.queue_capacity, "dropped": queue_dropped},
            "db": {"backend": "sqlite-memory", "writes": 1, "recovery_scanned": 1, "recovered_expired": len(recovered)},
        },
        "batch": {"fanout": config.batch_fanout, "completed": completed, "offline": offline, "aggregate_state": aggregate, "per_target_semantics": "online completes; offline remains offline; successes are not rolled back"},
        "limits": {"max_input": MAX_INPUT, "max_event_log_capacity": MAX_EVENT_CAPACITY, "max_logical_work": MAX_WORK},
        "not_tested": ["real devices", "network", "200-device acceptance", "wall-clock RSS/CPU/event-loop latency", "load or soak"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("registered", 4), ("online", 3), ("active-control", 1), ("telemetry", 4), ("batch-fanout", 4), ("queue-capacity", 32), ("event-log-capacity", 32)):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=int, default=default)
    parser.add_argument("--seed", type=int, default=0, help="reported scenario seed; simulation remains deterministic")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(CapacityConfig(**{key: getattr(args, key) for key in CapacityConfig.__dataclass_fields__}))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
