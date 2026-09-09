"""POSIX USB Serial/JTAG adapter tests using a local pseudo-terminal."""

from __future__ import annotations

import asyncio
import json
import os
import pty
import select
from unittest.mock import patch

import pytest

from bridge import Bridge, FakeTransport
from bridge.errors import ProtocolError, TransportError
from bridge.transports.serial import UsbSerialTransport


def test_usb_serial_transport_frames_data_and_ignores_console_lines():
    async def scenario():
        master, slave = pty.openpty()
        transport = UsbSerialTransport(os.ttyname(slave), startup_grace_s=0)
        received: list[dict] = []

        async def receive(frame: dict) -> None:
            received.append(frame)

        try:
            await transport.open(receive)
            await transport.send({"schema": "lifeos.v1", "kind": "hello", "type": "hello.host"})
            ready, _, _ = select.select([master], [], [], 0.5)
            assert ready
            assert json.loads(os.read(master, 4096)) == {
                "schema": "lifeos.v1",
                "kind": "hello",
                "type": "hello.host",
            }

            os.write(master, b"LIFEOS_HIL_READY boot diagnostics\n")
            os.write(master, b'{"schema":"lifeos.v1","kind":"event","type":"health.report",'
                     b'"event_id":"e1","device_id":"d1","seq":0,"ts_ms":1,"payload":{}}\n')
            for _ in range(20):
                await asyncio.sleep(0.01)
                if received:
                    break
            assert received == [{
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "health.report",
                "event_id": "e1",
                "device_id": "d1",
                "seq": 0,
                "ts_ms": 1,
                "payload": {},
            }]

            with pytest.raises(ProtocolError):
                await transport.send({"payload": "x" * (16 * 1024)})
        finally:
            await transport.close()
            os.close(master)
            try:
                os.close(slave)
            except OSError:
                pass

    asyncio.run(scenario())


def test_usb_serial_transport_notifies_bridge_on_reader_disconnect():
    async def scenario():
        master, slave = pty.openpty()
        transport = UsbSerialTransport(os.ttyname(slave), startup_grace_s=0)
        reasons: list[str] = []

        async def receive(_frame: dict) -> None:
            return

        async def disconnected(reason: str) -> None:
            reasons.append(reason)

        transport.set_disconnect_handler(disconnected)
        try:
            with patch(
                "bridge.transports.serial._read_once",
                side_effect=TransportError("USB serial read failed"),
            ):
                await transport.open(receive)
                for _ in range(50):
                    if reasons:
                        break
                    await asyncio.sleep(0.01)
            return reasons
        finally:
            await transport.close()
            try:
                os.close(master)
            except OSError:
                pass
            try:
                os.close(slave)
            except OSError:
                pass

    reasons = asyncio.run(scenario())
    assert reasons
    assert "USB serial" in reasons[0]


def test_failed_transport_open_is_closed_before_a_reconnect_can_retry():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport()
        transport.connected = True

        async def fail_open(_receiver):
            raise TransportError("open failed")

        transport.open = fail_open
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        with pytest.raises(TransportError, match="open failed"):
            await bridge.connect(device.device_id, transport)
        return transport, bridge.active_session_for_device(device.device_id)

    transport, active = asyncio.run(scenario())
    assert transport.connected is False
    assert active is None
