"""Transport protocol shared by USB, fake, and future Edge adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol


ReceiveCallback = Callable[[dict[str, Any]], Awaitable[None]]


class DeviceTransport(Protocol):
    transport_id: str

    async def open(self, receiver: ReceiveCallback) -> None: ...

    async def send(self, envelope: dict[str, Any]) -> None: ...

    async def send_priority(self, envelope: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...
