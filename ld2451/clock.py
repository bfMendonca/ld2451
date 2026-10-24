"""Receive-time clock: CLOCK_BOOTTIME on Linux, monotonic elsewhere."""

from __future__ import annotations

import sys
import time
from typing import Callable

Clock = Callable[[], int]


def default_clock() -> tuple[Clock, str]:
    """Return (clock function in ns, clock name)."""
    if sys.platform == "linux":
        boottime = time.CLOCK_BOOTTIME

        def boottime_ns() -> int:
            return time.clock_gettime_ns(boottime)

        try:
            boottime_ns()
        except OSError:  # pragma: no cover - very old kernels
            pass
        else:
            return boottime_ns, "CLOCK_BOOTTIME"
    return time.monotonic_ns, "monotonic"
