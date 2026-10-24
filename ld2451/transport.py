"""Byte transports: real serial port, recorded-log replay and in-memory loopback."""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

import serial

from .errors import TransportError

DEFAULT_BAUDRATE = 115200


@runtime_checkable
class Transport(Protocol):
    def open(self) -> None: ...

    def read(self, max_bytes: int) -> bytes:
        """Return up to ``max_bytes``; may return b"" after a short timeout. Never busy-waits."""
        ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...

    def flush_input(self) -> None: ...


@runtime_checkable
class TimestampedTransport(Transport, Protocol):
    """A transport that knows when its bytes were originally received (e.g. a replay)."""

    def read_timestamped(self, max_bytes: int) -> tuple[bytes, int | None]: ...


class SerialTransport:
    """pyserial-backed transport. The port is opened by :meth:`open`, not by the constructor."""

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 0.05) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: serial.Serial | None = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        if self.is_open:
            return
        try:
            self._serial = serial.Serial(
                self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
                write_timeout=1.0,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            self._serial = None
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    def _port(self) -> serial.Serial:
        if self._serial is None:
            raise TransportError(f"{self.port} is not open")
        return self._serial

    def read(self, max_bytes: int) -> bytes:
        port = self._port()
        try:
            n = min(max_bytes, max(1, port.in_waiting))
            return bytes(port.read(n))
        except (serial.SerialException, OSError, TypeError) as exc:
            # pyserial raises TypeError when the device disappears mid-read on some platforms.
            raise TransportError(f"read from {self.port} failed: {exc}") from exc

    def write(self, data: bytes) -> int:
        port = self._port()
        try:
            written = port.write(data)
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"write to {self.port} failed: {exc}") from exc
        return int(written or 0)

    def flush_input(self) -> None:
        try:
            self._port().reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"flush on {self.port} failed: {exc}") from exc

    def close(self) -> None:
        if self._serial is not None:
            with contextlib.suppress(serial.SerialException, OSError):
                self._serial.close()
            self._serial = None


class ReplayTransport:
    """Replays a log written by :class:`ld2451.recorder.Recorder`.

    Reads return the recorded chunks in order. With ``realtime=True`` the original
    pacing is reproduced. Writes are accepted and dropped. After the end of the log,
    reads wait ``timeout`` and return b"" and :attr:`exhausted` becomes True.
    """

    def __init__(self, path: str | Path, realtime: bool = False, timeout: float = 0.05) -> None:
        self.path = Path(path)
        self.realtime = realtime
        self.timeout = timeout
        self._records: deque[tuple[int, bytes]] = deque()
        self._opened = False
        self._first_t: int | None = None
        self._start_wall = 0.0
        self.exhausted = False

    def open(self) -> None:
        from .recorder import read_records

        if self._opened:
            return
        try:
            self._records = deque(read_records(self.path))
        except (OSError, ValueError) as exc:
            raise TransportError(f"cannot open replay {self.path}: {exc}") from exc
        self._opened = True
        self.exhausted = not self._records

    def read_timestamped(self, max_bytes: int) -> tuple[bytes, int | None]:
        if not self._opened:
            raise TransportError("replay is not open")
        if not self._records:
            self.exhausted = True
            time.sleep(self.timeout)
            return b"", None
        t, data = self._records[0]
        if self.realtime:
            if self._first_t is None:
                self._first_t, self._start_wall = t, time.monotonic()
            delay = (t - self._first_t) / 1e9 - (time.monotonic() - self._start_wall)
            if delay > 0:
                time.sleep(min(delay, self.timeout))
                if delay > self.timeout:
                    return b"", None
        if len(data) > max_bytes:
            self._records[0] = (t, data[max_bytes:])
            data = data[:max_bytes]
        else:
            self._records.popleft()
        return data, t

    def read(self, max_bytes: int) -> bytes:
        return self.read_timestamped(max_bytes)[0]

    def write(self, data: bytes) -> int:
        return len(data)

    def flush_input(self) -> None:
        pass

    def close(self) -> None:
        self._records.clear()
        self._opened = False


Responder = Callable[[bytes], Optional[bytes]]


class LoopbackTransport:
    """In-memory transport for tests and simulation.

    Bytes passed to :meth:`inject` are returned by :meth:`read`. Every :meth:`write`
    is recorded in :attr:`written` and, if a ``responder`` is set, its return value
    is injected as the radar's reply.
    """

    def __init__(self, responder: Responder | None = None, timeout: float = 0.05) -> None:
        self.responder = responder
        self.timeout = timeout
        self.written: list[bytes] = []
        self.is_open = False
        self.fail_reads = 0  # next N reads raise TransportError (to test reconnection)
        self.open_count = 0
        self._rx = bytearray()
        self._cond = threading.Condition()

    def open(self) -> None:
        self.is_open = True
        self.open_count += 1

    def inject(self, data: bytes) -> None:
        with self._cond:
            self._rx += data
            self._cond.notify_all()

    def read(self, max_bytes: int) -> bytes:
        with self._cond:
            if not self.is_open:
                raise TransportError("loopback is closed")
            if self.fail_reads > 0:
                self.fail_reads -= 1
                raise TransportError("simulated read failure")
            if not self._rx:
                self._cond.wait(self.timeout)
            data = bytes(self._rx[:max_bytes])
            del self._rx[:max_bytes]
            return data

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise TransportError("loopback is closed")
        self.written.append(bytes(data))
        if self.responder is not None:
            reply = self.responder(bytes(data))
            if reply:
                self.inject(reply)
        return len(data)

    def flush_input(self) -> None:
        with self._cond:
            self._rx.clear()

    def close(self) -> None:
        with self._cond:
            self.is_open = False
            self._cond.notify_all()
