#!/usr/bin/env python3
"""Safe Phase-1 HIL/soak harness.

The default is a deterministic dry run.  Real hardware is deliberately gated by
an explicit allow flag and a device-identification handshake; no motion command
is sent by this harness during discovery.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, asdict


@dataclass
class Metrics:
    samples: int = 0
    commands: int = 0
    rejected: int = 0
    safety_stops: int = 0
    feedback_frozen: int = 0
    disconnects: int = 0
    max_tick_ms: float = 0.0


class DryRunDevice:
    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics
        self.connected = True
        self.estopped = False
        self.paused = False
        self.torque_enabled = True

    def command(self, name: str, **payload: object) -> bool:
        self.metrics.commands += 1
        if name == "emergency_stop":
            self.estopped, self.torque_enabled = True, False
            self.metrics.safety_stops += 1
            return True
        if name == "resume" and not self.estopped and self.connected:
            self.paused, self.torque_enabled = False, True
            return True
        if not self.connected or self.estopped or self.paused:
            self.metrics.rejected += 1
            return False
        if name == "pause":
            self.paused, self.torque_enabled = True, False
            return True
        if name == "motion":
            yaw, pitch = payload.get("yaw", 0), payload.get("pitch", 45)
            if not isinstance(yaw, (int, float)) or not isinstance(pitch, (int, float)):
                self.metrics.rejected += 1
                return False
            if not (-90 <= yaw <= 90 and 5 <= pitch <= 85):
                self.metrics.rejected += 1
                return False
            return True
        self.metrics.rejected += 1
        return False

    def disconnect(self) -> None:
        self.connected = False
        self.torque_enabled = False
        self.metrics.disconnects += 1

    def freeze_feedback(self) -> None:
        """Model a frozen position sensor; safety must stop without blocking."""
        self.metrics.feedback_frozen += 1
        self.torque_enabled = False
        self.metrics.safety_stops += 1


def run_dry(duration_seconds: int = 8 * 60 * 60) -> dict[str, object]:
    metrics = Metrics()
    device = DryRunDevice(metrics)
    started = time.perf_counter()
    # Simulate the 20 Hz control cadence without waiting eight hours.
    for _ in range(duration_seconds * 20):
        metrics.samples += 1
    device.command("motion", yaw=0, pitch=45)
    device.command("emergency_stop")
    assert not device.torque_enabled and device.estopped
    device.command("pause")  # rejected after e-stop
    device = DryRunDevice(metrics)
    device.command("pause")
    assert not device.command("motion", yaw=0, pitch=45)
    device.command("resume")
    assert device.command("motion", yaw=0, pitch=45)
    assert not device.command("motion", yaw=91, pitch=45)
    device.disconnect()
    assert not device.command("motion", yaw=0, pitch=45)
    device.freeze_feedback()
    assert not device.torque_enabled
    metrics.max_tick_ms = (time.perf_counter() - started) * 1000
    return {"status": "PASS", "mode": "dry-run", "duration_seconds": duration_seconds,
            "control_hz": 20, "metrics": asdict(metrics)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--duration-seconds", type=int, default=8 * 60 * 60)
    parser.add_argument("--port", help="USB serial port (real HIL remains gated)")
    parser.add_argument("--allow-hardware", action="store_true")
    args = parser.parse_args()
    if not args.allow_hardware or not args.port:
        print(json.dumps(run_dry(args.duration_seconds), sort_keys=True))
        return 0
    # Identification is intentionally not implemented as a guess from a tty name.
    print(json.dumps({"status": "BLOCKED", "reason":
                      "no verified device handshake/test firmware evidence; no commands sent",
                      "port": args.port}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
