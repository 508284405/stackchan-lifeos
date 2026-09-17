"""Fail-closed loader for supervised real-device manual-control evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError


REQUIRED_CHECKS = frozenset(
    {
        "direction_mapping",
        "pitch_limits",
        "emergency_stop",
        "link_loss_stop",
        "video_stale_stop",
        "stall_detection",
    }
)


@dataclass(frozen=True)
class ManualControlEvidence:
    device_id: str
    hardware_id: str
    firmware_version: str
    checks: frozenset[str]


def load_manual_control_evidence(
    path: str | Path,
    *,
    device_id: str,
    hardware_id: str,
) -> ManualControlEvidence:
    evidence_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("manual-control evidence is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "device_id",
        "hardware_id",
        "firmware_version",
        "status",
        "checks",
    }:
        raise ValidationError("manual-control evidence has an invalid shape")
    checks = payload.get("checks")
    if (
        payload.get("schema") != "lifeos.manual-hil.v1"
        or payload.get("status") != "pass"
        or payload.get("device_id") != device_id
        or payload.get("hardware_id") != hardware_id
        or not isinstance(payload.get("firmware_version"), str)
        or not payload["firmware_version"]
        or not isinstance(checks, list)
        or not all(isinstance(check, str) for check in checks)
        or not REQUIRED_CHECKS.issubset(checks)
    ):
        raise ValidationError("manual-control evidence does not match this device and required HIL checks")
    return ManualControlEvidence(
        device_id=device_id,
        hardware_id=hardware_id,
        firmware_version=payload["firmware_version"],
        checks=frozenset(checks),
    )
