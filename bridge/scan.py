"""Local USB serial discovery for the Web Bridge.

The bridge itself stays free of platform serial enumeration (the transport
requires the caller to supply a concrete device path). This module is the one
place allowed to enumerate and briefly probe host serial ports so the Web
Console can offer "scan and add" instead of hand-typed paths.

Probing opens each candidate port for a read-only ``hello.host`` handshake.
Opening a USB CDC port asserts DTR and resets the ESP32-S3 Serial/JTAG
peripheral, so ports that already belong to an active bridge session are
reported ``in-use`` and never probed.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import platform
import select
import termios
import time
import tty
from typing import Any, Callable

from .domain import DiscoveryCandidate, SessionState
from .errors import ConflictError, TransportError
from .service import Bridge
from .transports.serial import UsbSerialTransport

SCHEMA = "lifeos.v1"
MAX_LINE_BYTES = 16 * 1024
# The firmware prints this boot banner on the HIL build; seeing it tells the
# probe the CDC reset window has passed even when no hello follows yet.
BOOT_BANNER = b"LIFEOS_HIL_READY"

PORT_PATTERNS = {
    "Darwin": ["/dev/cu.usbmodem*", "/dev/cu.usbserial*"],
    "Linux": ["/dev/ttyACM*", "/dev/ttyUSB*"],
}

PROBE_CAPABILITIES = frozenset({"status", "protocol", "safety"})


def candidate_ports(system: str | None = None) -> list[str]:
    """List USB-serial-looking /dev nodes for the host platform, sorted."""

    paths: set[str] = set()
    for pattern in PORT_PATTERNS.get(system or platform.system(), []):
        paths.update(glob.glob(pattern))
    return sorted(paths)


def _envelope(kind: str, typ: str, event_id: str, seq: int, payload: dict[str, Any]) -> bytes:
    value = {
        "schema": SCHEMA,
        "kind": kind,
        "type": typ,
        "event_id": event_id,
        "seq": seq,
        "ts_ms": int(time.time() * 1000),
        "payload": payload,
    }
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _validated_frame(line: bytes) -> dict[str, Any] | None:
    if len(line.rstrip(b"\r\n")) > MAX_LINE_BYTES:
        return None
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    required = {"schema", "kind", "type", "event_id", "seq", "ts_ms", "payload"}
    if not isinstance(value, dict) or not required.issubset(value):
        return None
    if value["schema"] != SCHEMA or value["kind"] not in {"event", "command", "ack", "error", "hello"}:
        return None
    return value


def _hello_identity(frame: dict[str, Any]) -> dict[str, Any]:
    payload = frame.get("payload", {})
    return {
        "device_id": frame.get("device_id") or payload.get("device_id"),
        "hardware_id": payload.get("hardware_id") or payload.get("mac"),
        "board": payload.get("board"),
        "firmware": payload.get("firmware"),
    }


def probe_port(path: str, *, timeout_s: float = 2.0, grace_s: float = 3.0) -> dict[str, Any]:
    """Identify one port with a read-only hello handshake; never actuates.

    Blocking; call from a worker thread. ``grace_s`` drains the CDC reset
    window, and the idempotent hello is retried once because the first copy
    can be swallowed by the reboot.
    """

    result: dict[str, Any] = {"path": path, "state": "no-response", "identity": None, "reason": None}
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        return {**result, "state": "busy", "reason": f"serial.open:{exc}"}
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

        data = bytearray()
        responses: list[dict[str, Any]] = []
        banner_seen = False

        def pump(seconds: float, stop_on_banner: bool = False) -> None:
            nonlocal banner_seen
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                ready, _, _ = select.select(
                    [fd], [], [], min(0.1, max(0.0, end - time.monotonic()))
                )
                if not ready:
                    continue
                data.extend(os.read(fd, 4096))
                while b"\n" in data:
                    raw, _, rest = data.partition(b"\n")
                    data[:] = rest
                    if BOOT_BANNER in raw:
                        banner_seen = True
                        if stop_on_banner:
                            return
                    frame = _validated_frame(raw)
                    if frame is not None:
                        responses.append(frame)

        pump(max(grace_s, 3.0), stop_on_banner=True)
        for _ in range(2):
            os.write(
                fd,
                _envelope(
                    "hello",
                    "hello.host",
                    "scan-hello-1",
                    1,
                    {
                        "protocol_versions": [SCHEMA],
                        "capabilities": ["status"],
                        "session_nonce": "usb-scan",
                        "media_enabled": False,
                    },
                ),
            )
            pump(timeout_s)
            if any(item.get("type") == "hello.device" for item in responses):
                break

        hello = next(
            (
                item
                for item in responses
                if item["kind"] == "hello" and item.get("type") == "hello.device"
            ),
            None,
        )
        if hello is None:
            return {**result, "reason": None if banner_seen else "no lifeos.v1 response"}
        return {**result, "state": "online", "identity": _hello_identity(hello)}
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)


async def scan_usb_ports(
    bridge: Bridge,
    *,
    timeout_s: float = 2.0,
    grace_s: float = 3.0,
    probe_fn: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Enumerate candidate ports; probe only the ones the bridge does not hold."""

    probe = probe_fn or (lambda path: probe_port(path, timeout_s=timeout_s, grace_s=grace_s))
    in_use = bridge.active_transport_paths()
    results: list[dict[str, Any]] = []
    for path in candidate_ports():
        if path in in_use:
            results.append({"path": path, "state": "in-use", "identity": None, "reason": None})
            continue
        results.append(await asyncio.to_thread(probe, path))
    return results


async def add_usb_device(
    bridge: Bridge,
    *,
    path: str,
    device_id: str,
    hardware_id: str,
    display_name: str | None = None,
    online_timeout_s: float = 8.0,
    transport_factory: Callable[[str], Any] | None = None,
) -> Any:
    """Register and connect one scanned USB device; returns the device record.

    Adding an already-registered hardware identity re-connects it, mirroring
    the idempotent registry claim. If the device does not reach an online
    session before ``online_timeout_s``, the session is torn down again.
    """

    if path in bridge.active_transport_paths():
        raise ConflictError(f"serial path is already used by an active session: {path}")
    transport_id = f"usb:{path}"
    candidate = DiscoveryCandidate(
        candidate_id=f"candidate-{hardware_id}",
        hardware_id=hardware_id,
        device_id=device_id,
        transport_id=transport_id,
        capabilities=PROBE_CAPABILITIES,
    )
    bridge.discover(candidate)
    record = bridge.claim(candidate.candidate_id, display_name=display_name)
    transport = (transport_factory or UsbSerialTransport)(path)
    session = await bridge.connect(record.device_id, transport)
    deadline = asyncio.get_running_loop().time() + online_timeout_s
    while asyncio.get_running_loop().time() < deadline:
        current = bridge.active_session_for_device(record.device_id)
        if current is not None and current.state is SessionState.ONLINE:
            return record
        await asyncio.sleep(0.05)
    await bridge.disconnect(session.session_id)
    raise TransportError(f"device did not complete hello before timeout: {path}")
