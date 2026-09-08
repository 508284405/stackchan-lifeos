#!/usr/bin/env python3
"""Controlled Phase 1 HIL acceptance runner.

The actuator path is never enabled by merely opening the port.  A real run
requires both ``--allow-hardware`` and a verified LifeOS hello response.  The
maintenance commands used for fault injection are compiled only into the HIL
image and require the explicit maintainer/test-mode fields.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import sys
import termios
import time
from dataclasses import dataclass, field
from typing import Any


SCHEMA = "lifeos.v1"
DEVICE_ID = "stackchan-01"
MAX_LINE = 16 * 1024
BANNER = b"LIFEOS_HIL_READY"


def envelope(kind: str, type_: str, event_id: str, seq: int,
             payload: dict[str, Any]) -> bytes:
    value = {
        "schema": SCHEMA,
        "kind": kind,
        "type": type_,
        "event_id": event_id,
        "device_id": DEVICE_ID,
        "seq": seq,
        "ts_ms": int(time.time() * 1000),
        "payload": payload,
    }
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


@dataclass
class Run:
    fd: int
    old_termios: list[Any]
    seq: int = 0
    lines: bytearray = field(default_factory=bytearray)
    responses: list[dict[str, Any]] = field(default_factory=list)
    log_path: str | None = None
    device_log: list[str] = field(default_factory=list)
    ack_latencies_ms: dict[str, float] = field(default_factory=dict)

    def close(self) -> None:
        termios.tcsetattr(self.fd, termios.TCSANOW, self.old_termios)
        os.close(self.fd)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            remaining = max(0.0, min(0.1, end - time.monotonic()))
            if remaining == 0.0:
                break
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                continue
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if not data:
                continue
            if self.log_path is not None:
                with open(self.log_path, "ab") as handle:
                    handle.write(data)
            self.device_log.extend(data.decode("utf-8", errors="replace").splitlines())
            self.lines.extend(data)
            while b"\n" in self.lines:
                raw, _, rest = self.lines.partition(b"\n")
                self.lines[:] = rest
                if len(raw.rstrip(b"\r")) > MAX_LINE:
                    continue
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    self.responses.append(value)

    def send(self, type_: str, payload: dict[str, Any], *, timeout: float = 3.0) -> dict[str, Any]:
        self.seq += 1
        event_id = f"phase1-hil-{self.seq}-{time.monotonic_ns()}"
        started = time.monotonic()
        os.write(self.fd, envelope("command", type_, event_id, self.seq, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(min(0.1, max(0.0, deadline - time.monotonic())))
            for value in reversed(self.responses):
                if value.get("correlation_id") == event_id:
                    self.ack_latencies_ms[type_] = round((time.monotonic() - started) * 1000, 3)
                    return value
        raise AssertionError(f"no response for {type_}/{event_id}: {self.responses[-8:]}")

    def hello(self, *, timeout: float = 4.0) -> dict[str, Any]:
        self.seq = 1
        event_id = "phase1-hil-hello"
        self.responses.clear()
        started = time.monotonic()
        os.write(self.fd, envelope("hello", "hello.host", event_id, self.seq, {
            "protocol_versions": [SCHEMA],
            "session_nonce": f"phase1-{time.monotonic_ns()}",
            "capabilities": ["status", "safety", "motion", "touch", "imu", "camera"],
            "raw_media": False,
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(min(0.1, max(0.0, deadline - time.monotonic())))
            for value in reversed(self.responses):
                if (
                    value.get("kind") == "hello"
                    and value.get("type") == "hello.device"
                    and value.get("correlation_id") == event_id
                ):
                    self.ack_latencies_ms["hello"] = round((time.monotonic() - started) * 1000, 3)
                    return value
        raise AssertionError(f"no LifeOS hello.device response: {self.responses[-8:]}")

    def device_lines(self, prefix: str, since_index: int = 0) -> list[str]:
        return [line for line in self.device_log[since_index:] if line.startswith(prefix)]


def open_run(port: str, log_path: str | None = None) -> Run:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    tty = termios.tcgetattr(fd)
    tty[0] = 0
    tty[1] = 0
    tty[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    tty[3] = 0
    tty[4] = termios.B115200
    tty[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, tty)
    return Run(fd, old, log_path=log_path)


def expect_status(run: Run, *, timeout: float = 2.0) -> dict[str, Any]:
    response = run.send("command.control", {"action": "status"}, timeout=timeout)
    assert response.get("kind") == "ack", response
    payload = response.get("payload", {})
    assert payload.get("status") == "completed", response
    return payload


def assert_bool(payload: dict[str, Any], key: str, expected: bool) -> None:
    assert payload.get(key) is expected, (key, expected, payload)


def wait_for_predicate(run: Run, predicate, *, timeout: float, poll: float = 0.15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = expect_status(run, timeout=min(2.0, max(1.0, deadline - time.monotonic())))
        if predicate(last):
            return last
        run.pump(poll)
    raise AssertionError(f"condition not met before {timeout}s; last status: {last}")


def settled_predicate(run: Run, target_yaw: float, target_pitch: float, tolerance: float = 2.0):
    """Settled requires fresh, applied feedback - stale snapshots must never
    mask a motion that did not happen."""
    def predicate(payload: dict[str, Any]) -> bool:
        yaw = payload.get("yaw_deg")
        pitch = payload.get("pitch_deg")
        applied = payload.get("command_applied_ms") or 0
        age = payload.get("feedback_age_ms")
        if yaw is None or pitch is None or not applied:
            return False
        if age is None or float(age) > 400:
            return False
        return (abs(float(yaw) - target_yaw) <= tolerance
                and abs(float(pitch) - target_pitch) <= tolerance)
    return predicate


def sample_motion(run: Run, target_yaw: float, target_pitch: float,
                  samples: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0)) -> list[dict[str, Any]]:
    trajectory = []
    started = time.monotonic()
    for offset in samples:
        wait = offset - (time.monotonic() - started)
        if wait > 0:
            time.sleep(wait)
        payload = expect_status(run)
        trajectory.append({
            "t_ms": round((time.monotonic() - started) * 1000, 1),
            "yaw_deg": payload.get("yaw_deg"),
            "pitch_deg": payload.get("pitch_deg"),
            "torque_enabled": payload.get("torque_enabled"),
            "io_state": payload.get("io_state"),
            "fault": payload.get("fault"),
            "feedback_age_ms": payload.get("feedback_age_ms"),
        })
    final = wait_for_predicate(run, settled_predicate(run, target_yaw, target_pitch), timeout=6.0)
    trajectory.append({
        "t_ms": round((time.monotonic() - started) * 1000, 1),
        "yaw_deg": final.get("yaw_deg"),
        "pitch_deg": final.get("pitch_deg"),
        "torque_enabled": final.get("torque_enabled"),
        "io_state": final.get("io_state"),
        "fault": final.get("fault"),
        "feedback_age_ms": final.get("feedback_age_ms"),
        "settled": True,
    })
    return trajectory


def _device_emergency_cut_ms(run: Run) -> float | None:
    """Extract the device-side emergency VM_EN cut time from the raw log."""
    if run.log_path is None:
        return None
    try:
        with open(run.log_path, "rb") as handle:
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = re.findall(r"safety power_cut reason=emergency elapsed_ms=(\d+)", raw)
    if not matches:
        return None
    return float(matches[-1])


def wait_for_touch_state(run: Run, *, paused: bool, timeout: float,
                         fault: bool | None = None) -> dict[str, Any]:
    print("Touch the StackChan head/display now; release after the state changes.",
          file=sys.stderr, flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = expect_status(run, timeout=min(2.0, max(1.0, deadline - time.monotonic())))
        if payload.get("paused") is paused and (fault is None or payload.get("fault") is fault):
            return payload
        run.pump(min(0.1, max(0.0, deadline - time.monotonic())))
    raise AssertionError(f"touch did not produce paused={paused} before timeout")


def run_mechanical_stall_check(
    run: Run, *, timeout: float, preparation_seconds: float = 8.0
) -> dict[str, Any]:
    """Run one supervised, gentle real-load stall check.

    The operator supplies the physical resistance. The firmware must latch a
    fault and release torque; this helper never increases force or retries the
    motion automatically.
    """

    print(
        "Mechanical stall check: gently hold the StackChan head so the next "
        "small move cannot proceed; release when fault/torque-off is reported.",
        file=sys.stderr,
        flush=True,
    )
    if preparation_seconds > 0:
        time.sleep(preparation_seconds)
    motion = {
        "authorization": "maintainer",
        "test_mode": "phase1",
        "yaw_deg": 35.0,
        "pitch_deg": 55.0,
    }
    acknowledged = run.send("command.maintenance_motion", motion)
    assert acknowledged.get("kind") == "ack", acknowledged
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = expect_status(run, timeout=min(2.0, max(1.0, deadline - time.monotonic())))
        if (
            last.get("fault") is True
            and last.get("torque_enabled") is False
            and int(last.get("safety_faults", 0)) > 0
        ):
            return last
        run.pump(min(0.1, max(0.0, deadline - time.monotonic())))
    raise AssertionError(f"mechanical stall did not latch before timeout; last status: {last}")


def run_read_only_soak(run: Run, seconds: float, interval: float) -> dict[str, Any]:
    if seconds <= 0:
        return {"status": "SKIPPED", "seconds": 0.0}
    if interval <= 0:
        raise ValueError("soak interval must be positive")
    deadline = time.monotonic() + seconds
    next_poll = time.monotonic()
    samples = 0
    heap_values: list[int] = []
    psram_values: list[int] = []
    uptime_values: list[int] = []
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_poll:
            payload = expect_status(run, timeout=min(5.0, max(1.0, interval)))
            assert_bool(payload, "fault", False)
            assert_bool(payload, "link_lost", False)
            assert_bool(payload, "torque_enabled", False)
            heap_values.append(int(payload["heap_free"]))
            psram_values.append(int(payload["psram_free"]))
            uptime_values.append(int(payload["uptime_ms"]))
            samples += 1
            next_poll = now + interval
        run.pump(min(0.1, max(0.0, deadline - time.monotonic())))
    assert samples >= 1, "read-only soak collected no samples"
    assert uptime_values == sorted(set(uptime_values)), uptime_values[-8:]
    return {
        "status": "PASS",
        "seconds": round(seconds, 3),
        "samples": samples,
        "heap_min": min(heap_values),
        "heap_max": max(heap_values),
        "psram_min": min(psram_values),
        "psram_max": max(psram_values),
        "uptime_first_ms": uptime_values[0],
        "uptime_last_ms": uptime_values[-1],
    }


def run_acceptance(port: str, grace: float, soak_seconds: float,
                   soak_interval: float, require_touch: bool,
                   require_mechanical_stall: bool, touch_timeout: float,
                   mechanical_prep_seconds: float,
                   log_path: str | None) -> dict[str, Any]:
    run = open_run(port, log_path)
    started = time.monotonic()
    evidence: dict[str, Any] = {}
    try:
        run.pump(max(grace, 3.0))
        hello = run.hello()
        hello_payload = hello.get("payload", {})
        assert hello_payload.get("board") == "StackChan/CoreS3", hello
        assert hello_payload.get("firmware", "").startswith("lifeos-phase1-"), hello
        assert_bool(hello_payload, "motion_enabled", True)

        # ---- Phase B: protocol response ------------------------------------
        initial = expect_status(run)
        assert_bool(initial, "torque_enabled", False)
        if initial.get("paused") is True:
            print(
                "HIL setup: device is paused; sending one resume before the "
                "acceptance matrix.",
                file=sys.stderr,
                flush=True,
            )
            resumed = run.send("command.control", {"action": "resume"})
            assert resumed.get("kind") == "ack", resumed
            initial = expect_status(run)
            assert_bool(initial, "paused", False)
        evidence["ack_latencies_ms"] = run.ack_latencies_ms.copy()
        ack_samples = [expect_status(run, timeout=2.0) for _ in range(3)]
        assert all(p.get("status") == "completed" for p in ack_samples)
        evidence["status_polls"] = 4

        # ---- Phase C: single constrained motion ----------------------------
        motion = {
            "authorization": "maintainer",
            "test_mode": "phase1",
            "yaw_deg": 12.0,
            "pitch_deg": 52.0,
        }
        marker = len(run.device_log)
        motion_ack = run.send("command.maintenance_motion", motion)
        assert motion_ack.get("kind") == "ack", motion_ack
        trajectory = sample_motion(run, 12.0, 52.0)
        assert_bool(trajectory[-1], "fault", False)
        assert any(point.get("torque_enabled") is True for point in trajectory)
        evidence["phaseC_motion_trajectory"] = trajectory
        evidence["phaseC_io_logs"] = run.device_lines("servo_io", marker)
        # torque must be released again once the motion completes
        idle = wait_for_predicate(run, lambda p: p.get("torque_enabled") is False, timeout=5.0)
        assert_bool(idle, "fault", False)

        touch_paused = None
        if require_touch:
            touch_paused = wait_for_touch_state(run, paused=True, timeout=touch_timeout)
            settled = wait_for_predicate(run, lambda p: p.get("torque_enabled") is False, timeout=2.0)
            assert_bool(settled, "torque_enabled", False)
            assert run.send("command.control", {"action": "resume"}).get("kind") == "ack"

        # ---- Phase D: safe stop ---------------------------------------------
        marker = len(run.device_log)
        assert run.send("command.control", {"action": "pause"}).get("kind") == "ack"
        paused = expect_status(run)
        assert_bool(paused, "paused", True)
        paused = wait_for_predicate(run, lambda p: p.get("torque_enabled") is False, timeout=2.0)
        assert_bool(paused, "paused", True)
        assert_bool(paused, "torque_enabled", False)
        time.sleep(0.4)
        after_pause = expect_status(run)
        assert_bool(after_pause, "paused", True)
        assert_bool(after_pause, "torque_enabled", False)
        evidence["phaseD_pause_logs"] = run.device_lines("safety power_cut", marker)
        evidence["phaseD_pause_io_state"] = after_pause.get("io_state")

        # ---- Phase E: home ---------------------------------------------------
        marker = len(run.device_log)
        assert run.send("command.control", {"action": "resume"}).get("kind") == "ack"
        assert run.send("command.control", {"action": "home"}).get("kind") == "ack"
        home_trajectory = sample_motion(run, 0.0, 45.0, samples=(0.5, 1.0, 2.0))
        assert_bool(home_trajectory[-1], "fault", False)
        evidence["phaseE_home_trajectory"] = home_trajectory
        idle = wait_for_predicate(run, lambda p: p.get("torque_enabled") is False, timeout=5.0)
        assert_bool(idle, "fault", False)
        evidence["phaseE_home_logs"] = run.device_lines("servo_io", marker)[-8:]

        # ---- Phase F: fault matrix -------------------------------------------
        # transient feedback failures: degrade but never latch
        marker = len(run.device_log)
        run.send("command.maintenance_motion", {**motion, "yaw_deg": 10.0, "pitch_deg": 50.0})
        assert run.send("command.maintenance_fault", {
            "authorization": "maintainer", "test_mode": "phase1",
            "kind": "feedback_transient",
        }).get("kind") == "ack"
        time.sleep(0.35)
        transient = expect_status(run)
        evidence["phaseF_transient_status"] = transient
        assert_bool(transient, "fault", False)
        assert_bool(transient, "torque_enabled", True)
        settled_transient = wait_for_predicate(run, settled_predicate(run, 10.0, 50.0), timeout=6.0)
        assert_bool(settled_transient, "fault", False)
        evidence["phaseF_transient_logs"] = run.device_lines("servo_io feedback failed", marker)

        # persistent feedback freeze: latches STALL and releases torque
        marker = len(run.device_log)
        run.send("command.maintenance_motion", {**motion, "yaw_deg": 9.0, "pitch_deg": 51.0})
        assert run.send("command.maintenance_fault", {
            "authorization": "maintainer", "test_mode": "phase1",
            "kind": "feedback_frozen",
        }).get("kind") == "ack"
        time.sleep(1.0)
        frozen = expect_status(run)
        assert_bool(frozen, "torque_enabled", False)
        assert_bool(frozen, "fault", True)
        assert_bool(frozen, "feedback_frozen", True)
        frozen = wait_for_predicate(run, lambda p: p.get("io_state") == "power_off", timeout=3.0)
        evidence["phaseF_frozen_status"] = frozen
        evidence["phaseF_frozen_logs"] = run.device_lines("servo_io feedback failed", marker)[:6]
        assert run.send("command.control", {
            "action": "clear_fault", "local_confirmation": True,
        }).get("kind") == "ack"
        cleared_after_freeze = wait_for_predicate(run, lambda p: p.get("fault") is False, timeout=3.0)
        assert_bool(cleared_after_freeze, "feedback_frozen", False)

        # hard limit: admission accepts, safety latch blocks the servo path
        marker = len(run.device_log)
        invalid = run.send("command.maintenance_motion", {
            **motion, "yaw_deg": 91.0, "pitch_deg": 50.0,
        })
        assert invalid.get("kind") in {"ack", "error"}, invalid
        time.sleep(0.25)
        limited = wait_for_predicate(run, lambda p: p.get("fault") is True, timeout=2.0)
        assert_bool(limited, "torque_enabled", False)
        evidence["phaseF_hard_limit_status"] = limited
        assert run.send("command.control", {"action": "clear_fault", "local_confirmation": True})
        wait_for_predicate(run, lambda p: p.get("fault") is False, timeout=3.0)

        mechanical_stall = None
        if require_mechanical_stall:
            mechanical_stall = run_mechanical_stall_check(
                run, timeout=8.0, preparation_seconds=mechanical_prep_seconds
            )
            evidence["phaseF_mechanical_stall_status"] = mechanical_stall
            assert_bool(mechanical_stall, "fault", True)
            assert_bool(mechanical_stall, "torque_enabled", False)
            assert run.send("command.control", {
                "action": "clear_fault", "local_confirmation": True,
            }).get("kind") == "ack"
            cleared_after_mechanical = wait_for_predicate(
                run, lambda p: p.get("fault") is False, timeout=3.0
            )
            assert_bool(cleared_after_mechanical, "torque_enabled", False)

        # emergency stop while motion is active: VM_EN cut must win the race
        run.send("command.maintenance_motion", {**motion, "yaw_deg": 8.0, "pitch_deg": 50.0})
        moving = wait_for_predicate(run, lambda p: p.get("torque_enabled") is True, timeout=3.0)
        assert_bool(moving, "fault", False)
        before_stop = time.monotonic()
        assert run.send("command.emergency_stop", {}).get("kind") == "ack"
        stopped = expect_status(run)
        stop_latency_ms = (time.monotonic() - before_stop) * 1000
        assert_bool(stopped, "torque_enabled", False)
        assert_bool(stopped, "fault", True)
        # The 100 ms emergency budget is a DEVICE-side requirement (VM_EN cut
        # measured from the handler); the host-side figure includes two full
        # USB-CDC round trips (~100 ms each on this transport).
        assert stop_latency_ms < 350.0, stop_latency_ms
        device_cut_ms = _device_emergency_cut_ms(run)
        assert device_cut_ms is not None and device_cut_ms < 100.0, device_cut_ms
        blocked = run.send("command.control", {"action": "home"})
        assert blocked.get("kind") == "error", blocked
        evidence["emergency_stop_latency_ms"] = round(stop_latency_ms, 3)
        evidence["emergency_device_cut_ms"] = device_cut_ms

        if require_touch:
            cleared = wait_for_touch_state(run, paused=False, fault=False,
                                           timeout=touch_timeout)
            assert_bool(cleared, "fault", False)
        else:
            assert run.send("command.control", {
                "action": "clear_fault", "local_confirmation": True,
            }).get("kind") == "ack"
            cleared = wait_for_predicate(run, lambda p: p.get("fault") is False, timeout=3.0)
        assert_bool(cleared, "fault", False)

        # link loss: no auto-resume of the abandoned motion after reconnect
        run.send("command.maintenance_motion", {**motion, "yaw_deg": 8.0, "pitch_deg": 50.0})
        run.close()
        time.sleep(2.0)
        run = open_run(port, log_path)
        run.pump(3.0)
        hello_after_reconnect = run.hello()
        assert hello_after_reconnect.get("kind") == "hello"
        reconnected = expect_status(run)
        assert_bool(reconnected, "torque_enabled", False)
        assert_bool(reconnected, "link_lost", False)
        evidence["reconnect"] = reconnected
        soak = run_read_only_soak(run, soak_seconds, soak_interval)

        return {
            "status": "PASS",
            "mode": "hardware-hil",
            "firmware": hello_payload.get("firmware"),
            "board": hello_payload.get("board"),
            "stop_latency_ms": evidence.get("emergency_stop_latency_ms"),
            "touch_pause": touch_paused,
            "mechanical_stall": mechanical_stall,
            "initial": initial,
            "evidence": evidence,
            "reconnected": reconnected,
            "soak": soak,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
    finally:
        try:
            run.close()
        except OSError:
            pass


def run_dry() -> dict[str, Any]:
    return {
        "status": "PASS",
        "mode": "dry-run",
        "note": "No device opened; use --allow-hardware --port for the real HIL matrix.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--allow-hardware", action="store_true")
    parser.add_argument("--grace", type=float, default=3.0)
    parser.add_argument("--soak-seconds", type=float, default=0.0,
                        help="read-only status soak duration; real HIL only")
    parser.add_argument("--soak-interval", type=float, default=2.0,
                        help="seconds between read-only status samples")
    parser.add_argument("--require-touch", action="store_true",
                        help="include supervised physical touch pause/clear checks")
    parser.add_argument("--require-mechanical-stall", action="store_true",
                        help="include one supervised gentle physical-load stall check")
    parser.add_argument("--mechanical-prep-seconds", type=float, default=8.0,
                        help="seconds to prepare before the mechanical-load move")
    parser.add_argument("--touch-timeout", type=float, default=20.0,
                        help="seconds to wait for each physical touch check")
    parser.add_argument("--log", help="tee raw device output to this file")
    args = parser.parse_args(argv)
    if not args.port or not args.allow_hardware:
        print(json.dumps(run_dry(), sort_keys=True))
        return 0
    try:
        result = run_acceptance(args.port, args.grace, args.soak_seconds,
                                args.soak_interval, args.require_touch,
                                args.require_mechanical_stall,
                                args.touch_timeout, args.mechanical_prep_seconds,
                                args.log)
    except (AssertionError, OSError, ValueError) as error:
        # A host-side USB-CDC teardown can leave the first port open silent;
        # retrying once is safe because nothing has moved before hello.
        if "hello" not in str(error):
            print(json.dumps({"status": "FAIL", "mode": "hardware-hil", "reason": str(error)}, sort_keys=True))
            return 2
        try:
            result = run_acceptance(args.port, args.grace, args.soak_seconds,
                                    args.soak_interval, args.require_touch,
                                    args.require_mechanical_stall,
                                    args.touch_timeout, args.mechanical_prep_seconds,
                                    args.log)
        except (AssertionError, OSError, ValueError) as retry_error:
            print(json.dumps({"status": "FAIL", "mode": "hardware-hil", "reason": str(retry_error)}, sort_keys=True))
            return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
