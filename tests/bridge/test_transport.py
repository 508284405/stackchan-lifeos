"""Framing and discovery contract tests for the replaceable USB adapter."""

from __future__ import annotations

import asyncio
import json

import pytest

from bridge.domain import DiscoveryCandidate
from bridge.errors import ProtocolError
from bridge.transports.jsonl import UsbDiscovery, UsbJsonlTransport


class MemoryWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def test_usb_jsonl_transport_frames_and_enforces_size_limit():
    async def scenario():
        reader = asyncio.StreamReader()
        writer = MemoryWriter()
        received = []

        async def receive(frame):
            received.append(frame)

        transport = UsbJsonlTransport(reader, writer, transport_id="usb-test")
        await transport.open(receive)
        outbound = {"schema": "lifeos.v1", "kind": "hello", "type": "hello.host"}
        await transport.send(outbound)
        reader.feed_data((json.dumps({"in": True}) + "\n").encode())
        await asyncio.sleep(0)
        with pytest.raises(ProtocolError):
            await transport.send({"data": "x" * (16 * 1024)})
        await transport.close()
        return bytes(writer.data), received

    data, received = asyncio.run(scenario())
    assert data.endswith(b"\n")
    assert json.loads(data) == {"schema": "lifeos.v1", "kind": "hello", "type": "hello.host"}
    assert received == [{"in": True}]


def test_usb_discovery_keeps_platform_scanning_injected():
    candidate = DiscoveryCandidate(
        candidate_id="candidate-1",
        hardware_id="hw-1",
        device_id="device-1",
        transport_id="usb-1",
    )
    discovery = UsbDiscovery(lambda: [candidate])
    assert discovery.scan() == [candidate]
