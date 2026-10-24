"""Driver for the Hi-Link HLK-LD2451 radar. Importing this package has no side effects."""

from __future__ import annotations

import logging

__version__ = "0.1.0"

from .driver import LD2451
from .errors import (
    CommandRejected,
    CommandTimeout,
    ConfigSessionError,
    FrameError,
    LD2451Error,
    TransportError,
    UnverifiedCommandWarning,
    VerificationError,
)
from .framing import FrameDecoder, FrameKind, RawFrame
from .models import (
    ConfigSessionInfo,
    DetectionParams,
    Direction,
    FirmwareVersion,
    RadarReport,
    Sensitivity,
    Stats,
    Target,
)
from .protocol import Ack, CommandCode
from .recorder import Recorder, read_records
from .transport import (
    LoopbackTransport,
    ReplayTransport,
    SerialTransport,
    TimestampedTransport,
    Transport,
)

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "LD2451",
    "Ack",
    "CommandCode",
    "CommandRejected",
    "CommandTimeout",
    "ConfigSessionError",
    "ConfigSessionInfo",
    "DetectionParams",
    "Direction",
    "FirmwareVersion",
    "FrameDecoder",
    "FrameError",
    "FrameKind",
    "LD2451Error",
    "LoopbackTransport",
    "RadarReport",
    "RawFrame",
    "Recorder",
    "ReplayTransport",
    "Sensitivity",
    "SerialTransport",
    "Stats",
    "Target",
    "TimestampedTransport",
    "Transport",
    "TransportError",
    "UnverifiedCommandWarning",
    "VerificationError",
    "__version__",
    "read_records",
]
