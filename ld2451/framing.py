"""Byte-stream framing: a pure state machine with no I/O."""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass
from enum import Enum

from .protocol import (
    CMD_FOOTER,
    CMD_HEADER,
    DATA_FOOTER,
    DATA_HEADER,
    MAX_CMD_PAYLOAD,
    MAX_DATA_PAYLOAD,
)

_HEADER_LEN = 4
_LENGTH_LEN = 2
_FOOTER_LEN = 4
_PREFIX_LEN = _HEADER_LEN + _LENGTH_LEN
DEFAULT_MAX_BUFFER = 4096


class FrameKind(Enum):
    DATA = "data"
    COMMAND = "command"


_LAYOUT = {
    FrameKind.DATA: (DATA_HEADER, DATA_FOOTER, MAX_DATA_PAYLOAD),
    FrameKind.COMMAND: (CMD_HEADER, CMD_FOOTER, MAX_CMD_PAYLOAD),
}


@dataclass(frozen=True)
class RawFrame:
    kind: FrameKind
    payload: bytes
    raw: bytes
    t_rx_ns: int


class FrameDecoder:
    """Splits a byte stream into frames, resynchronising after garbage or corruption.

    Feed it every chunk read from the transport with the time the read returned.
    A frame's ``t_rx_ns`` is the time of the chunk that carried its first header byte.
    """

    def __init__(self, max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        if max_buffer < _PREFIX_LEN + MAX_CMD_PAYLOAD + _FOOTER_LEN:
            raise ValueError("max_buffer too small to hold the largest frame")
        self._max_buffer = max_buffer
        self._buf = bytearray()
        self._base = 0  # stream offset of self._buf[0]
        self._marks: deque[tuple[int, int]] = deque()  # (stream offset of chunk start, t_rx_ns)
        self.bytes_discarded = 0
        self.bad_footers = 0
        self.bad_lengths = 0
        self.data_frames = 0
        self.command_frames = 0

    def reset(self) -> None:
        """Drop buffered bytes (counted as discarded). Counters are kept."""
        self._discard(len(self._buf))

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def feed(self, chunk: bytes, t_rx_ns: int) -> list[RawFrame]:
        if chunk:
            self._marks.append((self._base + len(self._buf), t_rx_ns))
            self._buf += chunk
        frames: list[RawFrame] = []
        while True:
            frame = self._next_frame()
            if frame is None:
                break
            frames.append(frame)
        if len(self._buf) > self._max_buffer:
            self._discard(len(self._buf) - self._max_buffer)
        return frames

    def _discard(self, n: int) -> None:
        if n <= 0:
            return
        del self._buf[:n]
        self._base += n
        self.bytes_discarded += n
        while len(self._marks) > 1 and self._marks[1][0] <= self._base:
            self._marks.popleft()
        if not self._buf:
            self._marks.clear()

    def _time_at_base(self) -> int:
        while len(self._marks) > 1 and self._marks[1][0] <= self._base:
            self._marks.popleft()
        return self._marks[0][1]

    def _find_header(self) -> tuple[int, FrameKind | None]:
        best = -1
        best_kind: FrameKind | None = None
        for kind, (header, _, _) in _LAYOUT.items():
            i = self._buf.find(header)
            if i >= 0 and (best < 0 or i < best):
                best, best_kind = i, kind
        return best, best_kind

    def _keep_partial_header_tail(self) -> None:
        """No full header in buffer: keep only a tail that may grow into one."""
        keep = 0
        for header, _, _ in _LAYOUT.values():
            for n in range(min(_HEADER_LEN - 1, len(self._buf)), 0, -1):
                if self._buf.endswith(header[:n]):
                    keep = max(keep, n)
                    break
        self._discard(len(self._buf) - keep)

    def _next_frame(self) -> RawFrame | None:
        while True:
            idx, kind = self._find_header()
            if kind is None:
                self._keep_partial_header_tail()
                return None
            self._discard(idx)
            if len(self._buf) < _PREFIX_LEN:
                return None
            _, footer, max_payload = _LAYOUT[kind]
            (length,) = struct.unpack_from("<H", self._buf, _HEADER_LEN)
            if length > max_payload:
                self.bad_lengths += 1
                self._discard(1)
                continue
            total = _PREFIX_LEN + length + _FOOTER_LEN
            if len(self._buf) < total:
                return None
            if self._buf[total - _FOOTER_LEN : total] != footer:
                self.bad_footers += 1
                self._discard(1)
                continue
            raw = bytes(self._buf[:total])
            frame = RawFrame(
                kind=kind,
                payload=raw[_PREFIX_LEN : _PREFIX_LEN + length],
                raw=raw,
                t_rx_ns=self._time_at_base(),
            )
            # A consumed frame is not "discarded": move the window without counting.
            del self._buf[:total]
            self._base += total
            if not self._buf:
                self._marks.clear()
            if kind is FrameKind.DATA:
                self.data_frames += 1
            else:
                self.command_frames += 1
            return frame
