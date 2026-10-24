"""Raw read log: one line per transport read, ``t_rx_ns,hex``."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

HEADER = "t_rx_ns,hex"


class Recorder:
    """Writes raw reads as CSV lines. Thread-safe: write and flush may run on different threads."""

    def __init__(self, path: str | Path, header: bool = True) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fh: IO[str] | None = open(self.path, "w", encoding="ascii", newline="\n")  # noqa: SIM115
        if header:
            self._fh.write(HEADER + "\n")

    def write(self, t_rx_ns: int, data: bytes) -> None:
        with self._lock:
            if self._fh is None:
                raise ValueError("recorder is closed")
            if data:
                self._fh.write(f"{t_rx_ns},{data.hex()}\n")

    def flush(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> Recorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def parse_line(line: str) -> tuple[int, bytes] | None:
    """Parse one log line. Returns None for blank lines, comments and the header."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    t_str, _, hex_str = line.partition(",")
    if not t_str.strip().lstrip("-").isdigit():
        return None  # header line
    return int(t_str), bytes.fromhex(hex_str.strip())


def read_records(path: str | Path) -> Iterator[tuple[int, bytes]]:
    with open(path, encoding="ascii") as fh:
        for lineno, line in enumerate(fh, 1):
            try:
                record = parse_line(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}") from exc
            if record is not None:
                yield record
