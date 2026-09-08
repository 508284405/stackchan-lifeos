"""Bounded event log and snapshot/cursor semantics."""

from __future__ import annotations

import pytest

from bridge.events import CursorExpired, EventLog


def test_telemetry_coalesces_with_a_new_cursor_and_hides_internal_key():
    log = EventLog(max_events=8)
    first = log.append(type="telemetry.sampled", device_id="d1", telemetry_key="health", payload={"v": 1})
    second = log.append(type="telemetry.sampled", device_id="d1", telemetry_key="health", payload={"v": 2})

    events = log.since(first.cursor, device_ids=["d1"])
    assert second.cursor > first.cursor
    assert [event.to_dict()["payload"] for event in events] == [{"v": 2}]


def test_cursor_expiry_is_explicit_and_device_filter_isolated():
    log = EventLog(max_events=8)
    for index in range(10):
        log.append(type="command.state.changed", device_id=f"d{index % 2}", payload={"n": index})

    with pytest.raises(CursorExpired) as error:
        log.since(1)
    assert error.value.code == "resync_required"
    recent = log.since(0, device_ids=["d1"])
    assert recent
    assert all(event.device_id == "d1" for event in recent)


def test_full_critical_log_drops_new_telemetry_but_keeps_critical_events():
    log = EventLog(max_events=8)
    for index in range(8):
        log.append(type="command.state.changed", device_id="d1", payload={"n": index})
    dropped = log.append(
        type="telemetry.sampled",
        device_id="d1",
        telemetry_key="health",
        payload={"heap": 1},
    )

    assert dropped.cursor == 0
    assert len(log.since(0)) == 8
    assert all(event.type == "command.state.changed" for event in log.since(0))


def test_event_payload_is_bounded_and_finite():
    log = EventLog()
    with pytest.raises(ValueError):
        log.append(type="command.state.changed", payload={"data": "x" * (8 * 1024)})
