"""Standard-library USB Serial/JTAG JSONL transport for macOS/Linux hosts."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import select
import termios
import tty
from collections.abc import Awaitable, Callable
from typing import Any

from ..errors import ProtocolError, TransportError
from .jsonl import MAX_LINE_BYTES
from .base import ReceiveCallback


DisconnectCallback = Callable[[str], Awaitable[None]]
logger = logging.getLogger(__name__)


def _read_once(fd: int) -> bytes | None:
    try:
        ready, _, _ = select.select([fd], [], [], 0.1)
    except InterruptedError:
        return None
    except OSError as exc:
        raise TransportError("USB serial read failed") from exc
    if not ready:
        return None
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return None
    except OSError as exc:
        raise TransportError("USB serial read failed") from exc


def _wait_writable(fd: int) -> bool:
    try:
        ready, _, _ = select.select([], [fd], [], 0.5)
    except (InterruptedError, OSError):
        return False
    return bool(ready)


class UsbSerialTransport:
    """A narrow POSIX USB Serial/JTAG adapter with JSONL framing.

    The caller supplies the concrete device path and remains responsible for
    discovery/identity confirmation. Opening a CDC endpoint may reset the USB
    peripheral; ``startup_grace_s`` drains that boot window before Bridge sends
    its hello. No hardware-specific or third-party serial dependency is used.
    """

    host_test_only = False
    _WRITE_RETRY_WINDOW_S = 0.75

    def __init__(
        self,
        path: str,
        *,
        transport_id: str | None = None,
        startup_grace_s: float = 3.0,
        manual_control_verified: bool = False,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("serial path must be a non-empty string")
        if not isinstance(startup_grace_s, (int, float)) or startup_grace_s < 0:
            raise ValueError("startup_grace_s must be non-negative")
        self.path = path
        self.transport_id = transport_id or f"usb:{path}"
        self.hello_host_first = True
        self.startup_grace_s = float(startup_grace_s)
        # This marker is only set by an explicitly supervised real-device
        # launch. USB discovery/add remains read-only and cannot enable it.
        self.manual_control_verified = bool(manual_control_verified)
        self._fd: int | None = None
        self._old_termios: list[Any] | None = None
        self._receiver: ReceiveCallback | None = None
        self._disconnect_handler: DisconnectCallback | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = True
        self._buffer = bytearray()
        # Opt-in only: firmware diagnostics share the CDC endpoint but are not
        # protocol envelopes. Keeping them visible during a real HIL failure
        # avoids guessing why a session stopped receiving health frames.
        self._log_console = os.environ.get("LIFEOS_SERIAL_CONSOLE_LOG") == "1"

    def set_disconnect_handler(self, handler: DisconnectCallback | None) -> None:
        """Install the Bridge callback for a reader-side USB failure."""

        self._disconnect_handler = handler

    async def open(self, receiver: ReceiveCallback) -> None:
        if self._fd is not None:
            raise TransportError("USB serial transport is already open")
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            old_termios = termios.tcgetattr(fd)
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = termios.B115200
            attrs[5] = termios.B115200
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except (OSError, termios.error) as exc:
            try:
                os.close(fd)  # type: ignore[possibly-undefined]
            except (NameError, OSError):
                pass
            raise TransportError(f"cannot open USB serial path: {self.path}") from exc

        self._fd = fd
        self._old_termios = old_termios
        self._receiver = receiver
        self._closed = False
        self._buffer.clear()
        self._reader_task = asyncio.create_task(self._read_loop())
        if self.startup_grace_s:
            await asyncio.sleep(self.startup_grace_s)

    async def close(self) -> None:
        self._closed = True
        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        fd, old_termios = self._fd, self._old_termios
        self._fd = None
        self._old_termios = None
        self._receiver = None
        self._buffer.clear()
        if fd is not None:
            if old_termios is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, old_termios)
                except (OSError, termios.error):
                    pass
            try:
                os.close(fd)
            except OSError:
                pass

    async def send(self, envelope: dict[str, Any]) -> None:
        fd = self._fd
        if self._closed or fd is None:
            raise TransportError("USB serial transport is closed")
        try:
            raw = json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise ProtocolError("cannot encode non-finite or non-JSON envelope") from exc
        if len(raw) > MAX_LINE_BYTES:
            raise ProtocolError("envelope exceeds 16 KiB line limit")

        loop = asyncio.get_running_loop()
        write_deadline = loop.time() + self._WRITE_RETRY_WINDOW_S
        offset = 0
        while offset < len(raw):
            try:
                written = os.write(fd, raw[offset:])
            except BlockingIOError:
                if loop.time() >= write_deadline:
                    self._closed = True
                    raise TransportError("USB serial write timed out")
                writable = await loop.run_in_executor(None, _wait_writable, fd)
                if not writable:
                    continue
                continue
            except OSError as exc:
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR} and loop.time() < write_deadline:
                    await asyncio.sleep(0)
                    continue
                self._closed = True
                detail = getattr(exc, "errno", None)
                if detail is not None:
                    raise TransportError(f"USB serial write failed (errno={detail}): {exc}") from exc
                raise TransportError(f"USB serial write failed: {exc}") from exc
            except ValueError as exc:
                self._closed = True
                raise TransportError(f"USB serial write failed: {exc}") from exc
            if written <= 0:
                self._closed = True
                raise TransportError("USB serial write returned no progress")
            offset += written

    async def send_priority(self, envelope: dict[str, Any]) -> None:
        await self.send(envelope)

    async def _read_loop(self) -> None:
        fd = self._fd
        if fd is None:
            return
        loop = asyncio.get_running_loop()
        try:
            while not self._closed:
                data = await loop.run_in_executor(None, _read_once, fd)
                if data is None:
                    continue
                if not data:
                    # USB Serial/JTAG can return a transient empty read while
                    # CDC re-enumerates after reset. Treat it as no data; real
                    # disconnects are surfaced by read/write OSError or by the
                    # health-capable session freshness supervisor.
                    await asyncio.sleep(0.05)
                    continue
                self._buffer.extend(data)
                while b"\n" in self._buffer:
                    raw, _, rest = self._buffer.partition(b"\n")
                    self._buffer[:] = rest
                    raw = raw.rstrip(b"\r")
                    if len(raw) > MAX_LINE_BYTES:
                        raise ProtocolError("received envelope exceeds 16 KiB line limit")
                    try:
                        value = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # ESP32 USB Serial/JTAG also carries boot/diagnostic
                        # text on stdout. It is out-of-band console data, not
                        # a device envelope, and must not wedge the JSONL link.
                        if self._log_console:
                            text = raw.decode("utf-8", errors="replace").strip()
                            if text:
                                logger.warning("device console %s: %s", self.path, text[:512])
                        continue
                    if not isinstance(value, dict):
                        continue
                    if self._receiver is not None:
                        await self._receiver(value)
        except (ProtocolError, TransportError, ConnectionError, OSError) as exc:
            self._closed = True
            handler = self._disconnect_handler
            if handler is not None:
                try:
                    await handler(str(exc))
                except Exception:
                    # A transport callback must never keep the reader task
                    # alive after the underlying endpoint has failed.
                    pass
