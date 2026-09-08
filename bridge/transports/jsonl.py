"""Dependency-free JSONL transport and injected USB discovery boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from typing import Any

from ..domain import DiscoveryCandidate
from ..errors import ProtocolError, TransportError
from .base import ReceiveCallback


MAX_LINE_BYTES = 16 * 1024


class UsbJsonlTransport:
    """JSONL transport over an already-open async byte stream.

    Opening a serial device is intentionally injected by the caller. This keeps
    the bridge free of a platform-specific serial dependency while preserving
    the exact framing, size, and backpressure contract for a USB adapter.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        transport_id: str,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.transport_id = transport_id
        self._receiver: ReceiveCallback | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    async def open(self, receiver: ReceiveCallback) -> None:
        self._receiver = receiver
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        self.writer.close()
        wait_closed = getattr(self.writer, "wait_closed", None)
        if wait_closed is not None:
            await wait_closed()

    async def send(self, envelope: dict[str, Any]) -> None:
        if self._closed:
            raise TransportError("USB JSONL transport is closed")
        try:
            raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            ) + b"\n"
        except (TypeError, ValueError) as exc:
            raise ProtocolError("cannot encode non-finite or non-JSON envelope") from exc
        if len(raw) > MAX_LINE_BYTES:
            raise ProtocolError("envelope exceeds 16 KiB line limit")
        self.writer.write(raw)
        try:
            await self.writer.drain()
        except (ConnectionError, OSError) as exc:
            raise TransportError("USB JSONL write failed") from exc

    async def send_priority(self, envelope: dict[str, Any]) -> None:
        """Dedicated transport hook used by the Bridge emergency path."""

        await self.send(envelope)

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                raw = await self.reader.readline()
                if not raw:
                    return
                if len(raw) > MAX_LINE_BYTES:
                    raise ProtocolError("received envelope exceeds 16 KiB line limit")
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ProtocolError("received invalid JSONL envelope") from exc
                if not isinstance(value, dict):
                    raise ProtocolError("received non-object JSONL envelope")
                if self._receiver is not None:
                    await self._receiver(value)
        except (ProtocolError, ConnectionError, OSError):
            # A framing or stream failure closes this adapter; the bridge
            # freshness loop owns the session state transition.
            self._closed = True


class UsbDiscovery:
    """Enumerate USB candidates through a platform-specific injected scanner."""

    def __init__(self, scanner: Callable[[], Iterable[DiscoveryCandidate]]) -> None:
        self.scanner = scanner

    def scan(self) -> list[DiscoveryCandidate]:
        return list(self.scanner())
