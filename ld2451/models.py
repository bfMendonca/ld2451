"""Immutable data models. Fields are plain ints/bools/bytes so they map easily to ROS messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name}={value} out of range [{low}, {high}]")


class Direction(IntEnum):
    """Movement direction filter for :class:`DetectionParams` (manual p. 4)."""

    AWAY_ONLY = 0x00
    APPROACH_ONLY = 0x01
    BOTH = 0x02


@dataclass(frozen=True)
class Target:
    """One detected target (manual p. 9)."""

    angle_deg: int
    distance_m: int
    approaching: bool
    speed_kmh: int
    snr: int

    def __post_init__(self) -> None:
        _check_range("angle_deg", self.angle_deg, -128, 127)
        _check_range("distance_m", self.distance_m, 0, 255)
        _check_range("speed_kmh", self.speed_kmh, 0, 255)
        _check_range("snr", self.snr, 0, 255)


@dataclass(frozen=True)
class RadarReport:
    """One data frame. ``t_rx_ns`` is when the read carrying its first header byte returned."""

    t_rx_ns: int
    targets: tuple[Target, ...]
    header_bytes: bytes
    raw: bytes

    def __post_init__(self) -> None:
        if len(self.targets) > 5:
            raise ValueError(f"at most 5 targets, got {len(self.targets)}")
        if self.header_bytes and len(self.header_bytes) != 2:
            raise ValueError("header_bytes must be empty or 2 bytes")

    @property
    def reported_count(self) -> int:
        """Target count as sent by the radar in payload byte 0 (0 for an empty frame)."""
        return self.header_bytes[0] if self.header_bytes else 0

    @property
    def approach_alarm(self) -> bool:
        """Payload byte 1: a target is approaching."""
        return bool(self.header_bytes) and self.header_bytes[1] == 0x01


@dataclass(frozen=True)
class DetectionParams:
    """Target detection parameters, commands 0x0002 / 0x0012 (manual p. 4-5)."""

    max_distance_m: int
    direction: Direction
    min_speed_kmh: int
    no_target_delay_s: int

    def __post_init__(self) -> None:
        _check_range("max_distance_m", self.max_distance_m, 0x0A, 0xFF)
        Direction(self.direction)
        _check_range("min_speed_kmh", self.min_speed_kmh, 0x00, 0x78)
        _check_range("no_target_delay_s", self.no_target_delay_s, 0x00, 0xFF)


@dataclass(frozen=True)
class Sensitivity:
    """Sensitivity parameters, commands 0x0003 / 0x0013 (manual p. 5-6).

    ``snr_threshold`` 0 means the firmware default (4); 3-8 raise the threshold.
    """

    trigger_count: int
    snr_threshold: int
    extended1: int = 0
    extended2: int = 0

    def __post_init__(self) -> None:
        _check_range("trigger_count", self.trigger_count, 1, 0x0A)
        if self.snr_threshold != 0:
            _check_range("snr_threshold", self.snr_threshold, 3, 8)
        _check_range("extended1", self.extended1, 0, 0xFF)
        _check_range("extended2", self.extended2, 0, 0xFF)


@dataclass(frozen=True)
class FirmwareVersion:
    """Firmware information, command 0x00A0 (manual p. 6)."""

    firmware_type: int
    major: int
    minor: int

    def __post_init__(self) -> None:
        _check_range("firmware_type", self.firmware_type, 0, 0xFFFF)
        _check_range("major", self.major, 0, 0xFFFF)
        _check_range("minor", self.minor, 0, 0xFFFFFFFF)

    @property
    def version(self) -> str:
        """Version string as printed by the manual, e.g. ``V1.01.24051510``."""
        return f"V{self.major >> 8}.{self.major & 0xFF:02x}.{self.minor:08x}"


@dataclass(frozen=True)
class ConfigSessionInfo:
    """Data returned by the enable-configuration ACK (manual p. 3)."""

    protocol_version: int
    buffer_size: int


@dataclass(frozen=True)
class Stats:
    """Counter snapshot for monitoring."""

    data_frames: int = 0
    command_frames: int = 0
    bytes_discarded: int = 0
    bad_footers: int = 0
    bad_lengths: int = 0
    invalid_payloads: int = 0
    timeouts: int = 0
    dropped_reports: int = 0
    callback_errors: int = 0
    reconnects: int = 0
