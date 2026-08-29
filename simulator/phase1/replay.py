"""Deterministic ``lifeos.v1`` JSONL replay for Phase 1 acceptance."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MAX_LINE_BYTES = 16 * 1024
KINDS = {"event", "command", "ack", "error", "hello"}
CONTROL_ACTIONS = {"pause", "resume", "home", "status", "clear_fault"}
COMMAND_TYPES = {"command.control", "command.emergency_stop", "command.intent",
                 "command.maintenance_motion"}
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


@dataclass
class ReplayResult:
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    motion_count: int = 0
    safety_stops: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rejected == 0


class ReplayDevice:
    """Stateful virtual device; validated payloads never cause real side effects."""

    def __init__(self, *, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms
        self.connected = True
        self.hello_complete = False
        self.device_id: str | None = None
        self.last_seq: int | None = None
        self.seen_ids: deque[str] = deque(maxlen=16)
        self.paused = False
        self.fault_latched: str | None = None
        self.torque_enabled = False
        self.target_yaw, self.target_pitch = 0.0, 45.0
        self.result = ReplayResult()

    def _reject(self, reason: str) -> str:
        self.result.rejected += 1
        self.result.reasons.append(reason)
        return "rejected"

    def disconnect(self, now_ms: int | None = None) -> None:
        self.now_ms = self.now_ms if now_ms is None else now_ms
        self.connected = self.hello_complete = False
        self.device_id = None
        self.last_seq = None
        self.seen_ids.clear()
        self.torque_enabled = False
        self.result.safety_stops += 1

    def connect(self, now_ms: int | None = None) -> None:
        self.now_ms = self.now_ms if now_ms is None else now_ms
        self.connected = True
        self.hello_complete = False
        self.device_id = None
        self.last_seq = None
        self.seen_ids.clear()

    def inject_fault(self, code: str, now_ms: int | None = None) -> None:
        self.now_ms = self.now_ms if now_ms is None else now_ms
        self.fault_latched = code
        self.torque_enabled = False
        self.result.safety_stops += 1

    def snapshot(self) -> dict[str, Any]:
        return {"connected": self.connected, "hello_complete": self.hello_complete,
                "last_seq": self.last_seq, "paused": self.paused,
                "fault_latched": self.fault_latched,
                "torque_enabled": self.torque_enabled,
                "target_yaw": self.target_yaw, "target_pitch": self.target_pitch}

    def feed(self, record: dict[str, Any]) -> str:
        try:
            raw = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            return self._reject("json.invalid_number")
        if len(raw) > MAX_LINE_BYTES:
            return self._reject("message.too_large")
        required = {"schema", "kind", "type", "event_id", "seq", "ts_ms", "payload"}
        if not required.issubset(record):
            return self._reject("schema.required")
        if record["schema"] != "lifeos.v1" or record["kind"] not in KINDS:
            return self._reject("schema.unsupported")
        if (not isinstance(record["type"], str) or len(record["type"]) > 64 or
                TYPE_RE.fullmatch(record["type"]) is None):
            return self._reject("schema.type")
        if not record.get("device_id"):
            return self._reject("schema.device_id")
        if self.hello_complete and record["device_id"] != self.device_id:
            return self._reject("schema.device_id_mismatch")
        event_id = record["event_id"]
        if not isinstance(event_id, str) or not event_id:
            return self._reject("schema.event_id")
        if record["kind"] == "hello":
            if not self.connected or record["type"] not in {"hello.device", "hello.host"}:
                return self._reject("hello.invalid")
            if self.hello_complete:
                # A valid hello re-establishes the session (seq renegotiation
                # after reconnect). Only session bookkeeping is cleared;
                # safety state such as pause or latched faults is untouched.
                self.hello_complete = False
                self.last_seq = None
                self.seen_ids.clear()
        if event_id in self.seen_ids:
            self.result.accepted += 1
            self.result.duplicates += 1
            return "duplicate"
        if not self._check_seq(record["seq"]):
            return "rejected"
        if not isinstance(record["payload"], dict):
            return self._reject("schema.payload")
        if record["kind"] == "hello":
            if not self.connected or record["type"] not in {"hello.device", "hello.host"}:
                return self._reject("hello.invalid")
            self.hello_complete = True
            self.device_id = record["device_id"]
        elif record["kind"] == "command" and self._command(record) == "rejected":
            return "rejected"
        self.seen_ids.append(event_id)
        self.result.accepted += 1
        return "accepted"

    def _check_seq(self, seq: Any) -> bool:
        if not isinstance(seq, int) or seq < 0:
            self._reject("seq.invalid")
            return False
        if self.last_seq is not None and seq != self.last_seq + 1:
            self._reject("seq.out_of_order")
            return False
        self.last_seq = seq
        return True

    def _command(self, record: dict[str, Any]) -> str:
        command_type, payload = record["type"], record["payload"]
        if command_type not in COMMAND_TYPES:
            return self._reject("command.unsupported")
        if command_type == "command.emergency_stop":
            self.inject_fault("EMERGENCY_STOP")
            return "accepted"
        if not self.connected or not self.hello_complete:
            return self._reject("link.not_ready")
        issued, expires = payload.get("issued_at_ms"), payload.get("expires_at_ms")
        if issued is not None or expires is not None:
            if not isinstance(issued, int) or not isinstance(expires, int) or expires <= issued:
                return self._reject("schema.ttl")
            if expires <= self.now_ms or self.now_ms < issued or self.now_ms - issued > 1500:
                return self._reject("command.expired")
        if self.fault_latched:
            if command_type != "command.control" or payload.get("action") != "clear_fault":
                return self._reject("fault.latched")
            if not payload.get("local_confirmation", False):
                return self._reject("fault.local_confirmation_required")
            self.fault_latched = None
            return "accepted"
        if command_type == "command.control":
            action = payload.get("action")
            if action not in CONTROL_ACTIONS:
                return self._reject("command.unsupported")
            if action == "pause":
                self.paused, self.torque_enabled = True, False
            elif action == "resume":
                self.paused = False
            elif action == "home" and not self.paused:
                self.target_yaw, self.target_pitch = 0.0, 45.0
                self.torque_enabled = True
                self.result.motion_count += 1
            return "accepted"
        yaw, pitch = payload.get("yaw_deg"), payload.get("pitch_deg")
        for value in (yaw, pitch):
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                return self._reject("motion.invalid_number")
        if ((yaw is not None and abs(yaw) > 90.0) or
                (pitch is not None and not 5.0 <= pitch <= 85.0)):
            self.torque_enabled = False
            self.result.safety_stops += 1
            return self._reject("motion.hard_limit")
        if yaw is not None:
            self.target_yaw = float(yaw)
        if pitch is not None:
            self.target_pitch = float(pitch)
        self.torque_enabled = True
        self.result.motion_count += 1
        return "accepted"

    def replay(self, records: Iterable[dict[str, Any]]) -> ReplayResult:
        for record in records:
            if record.get("_control") == "disconnect":
                self.disconnect()
            elif record.get("_control") == "connect":
                self.connect()
            elif record.get("_control"):
                self._reject(f"control.unknown:{record['_control']}")
            else:
                self.feed(record)
        return self.result


ReplayEngine = ReplayDevice


def replay_file(path: Path) -> ReplayResult:
    device, records = ReplayDevice(), []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if len(line.encode()) > MAX_LINE_BYTES:
                device._reject(f"line.too_large:{number}")
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                device._reject(f"json.invalid:{number}")
                continue
            if not isinstance(record, dict):
                device._reject(f"json.object:{number}")
                continue
            records.append(record)
    return device.replay(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args(argv)
    result = replay_file(args.scenario)
    print(json.dumps({"accepted": result.accepted, "duplicates": result.duplicates,
                      "motion_count": result.motion_count, "rejected": result.rejected,
                      "safety_stops": result.safety_stops, "reasons": result.reasons},
                     sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
