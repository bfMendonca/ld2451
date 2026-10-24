"""Protocol constants and pure encode/decode functions (manual V1.03)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .errors import FrameError
from .models import (
    ConfigSessionInfo,
    DetectionParams,
    Direction,
    FirmwareVersion,
    RadarReport,
    Sensitivity,
    Target,
)

DATA_HEADER = b"\xf4\xf3\xf2\xf1"
DATA_FOOTER = b"\xf8\xf7\xf6\xf5"
CMD_HEADER = b"\xfd\xfc\xfb\xfa"
CMD_FOOTER = b"\x04\x03\x02\x01"

MAX_TARGETS = 5
TARGET_SIZE = 5
REPORT_HEADER_SIZE = 2
MAX_DATA_PAYLOAD = REPORT_HEADER_SIZE + MAX_TARGETS * TARGET_SIZE
MAX_CMD_PAYLOAD = 64

ACK_FLAG = 0x0100
ANGLE_OFFSET = 0x80


class CommandCode(IntEnum):
    """Command words documented in the manual."""

    SET_PARAMS = 0x0002
    SET_SENSITIVITY = 0x0003
    GET_PARAMS = 0x0012
    GET_SENSITIVITY = 0x0013
    GET_FIRMWARE = 0x00A0
    SET_BAUDRATE = 0x00A1
    FACTORY_RESET = 0x00A2
    RESTART = 0x00A3
    END_CONFIG = 0x00FE
    ENABLE_CONFIG = 0x00FF


BAUDRATE_INDEX = {
    9600: 0x0001,
    19200: 0x0002,
    38400: 0x0003,
    57600: 0x0004,
    115200: 0x0005,
    230400: 0x0006,
    256000: 0x0007,
    460800: 0x0008,
}

ENABLE_CONFIG_VALUE = b"\x01\x00"


@dataclass(frozen=True)
class Ack:
    """A decoded ACK. ``code`` is the original command word, without ``ACK_FLAG``."""

    code: int
    status: int
    data: bytes

    @property
    def ok(self) -> bool:
        return self.status == 0


def encode_command(code: int, value: bytes = b"") -> bytes:
    """Build a complete command frame."""
    if not 0 <= code <= 0xFFFF:
        raise ValueError(f"command code out of range: {code}")
    body = struct.pack("<H", code) + bytes(value)
    if len(body) > MAX_CMD_PAYLOAD:
        raise ValueError(f"command body too long: {len(body)} bytes")
    return CMD_HEADER + struct.pack("<H", len(body)) + body + CMD_FOOTER


def decode_command(payload: bytes) -> tuple[int, bytes]:
    """Split a host->radar command payload into (code, value). Used by simulators and tests."""
    if len(payload) < 2:
        raise FrameError(f"command payload too short: {len(payload)} bytes")
    (code,) = struct.unpack_from("<H", payload)
    return code, bytes(payload[2:])


def encode_ack(code: int, status: int = 0, data: bytes = b"") -> bytes:
    """Build a complete ACK frame, as the radar would send it."""
    body = struct.pack("<HH", code | ACK_FLAG, status) + bytes(data)
    return CMD_HEADER + struct.pack("<H", len(body)) + body + CMD_FOOTER


def decode_ack(payload: bytes) -> Ack:
    """Decode the payload of a radar->host command frame."""
    if len(payload) < 4:
        raise FrameError(f"ACK payload too short: {len(payload)} bytes")
    word, status = struct.unpack_from("<HH", payload)
    if not word & ACK_FLAG:
        raise FrameError(f"command word 0x{word:04X} lacks the ACK flag")
    return Ack(code=word & ~ACK_FLAG & 0xFFFF, status=status, data=bytes(payload[4:]))


def decode_target(chunk: bytes) -> Target:
    if len(chunk) != TARGET_SIZE:
        raise FrameError(f"target block must be {TARGET_SIZE} bytes, got {len(chunk)}")
    angle, distance, direction, speed, snr = chunk
    if direction not in (0x00, 0x01):
        raise FrameError(f"invalid direction byte 0x{direction:02X}")
    return Target(
        angle_deg=angle - ANGLE_OFFSET,
        distance_m=distance,
        approaching=direction == 0x00,
        speed_kmh=speed,
        snr=snr,
    )


def encode_target(target: Target) -> bytes:
    return bytes(
        (
            target.angle_deg + ANGLE_OFFSET,
            target.distance_m,
            0x00 if target.approaching else 0x01,
            target.speed_kmh,
            target.snr,
        )
    )


def decode_report(payload: bytes, t_rx_ns: int, raw: bytes = b"") -> RadarReport:
    """Decode a data frame payload. An empty payload means no target."""
    n = len(payload)
    if n == 0:
        return RadarReport(t_rx_ns=t_rx_ns, targets=(), header_bytes=b"", raw=bytes(raw))
    if n < REPORT_HEADER_SIZE or (n - REPORT_HEADER_SIZE) % TARGET_SIZE:
        raise FrameError(f"invalid report payload length {n}")
    count = (n - REPORT_HEADER_SIZE) // TARGET_SIZE
    if count > MAX_TARGETS:
        raise FrameError(f"too many targets: {count}")
    targets = tuple(
        decode_target(payload[i : i + TARGET_SIZE])
        for i in range(REPORT_HEADER_SIZE, n, TARGET_SIZE)
    )
    return RadarReport(
        t_rx_ns=t_rx_ns,
        targets=targets,
        header_bytes=bytes(payload[:REPORT_HEADER_SIZE]),
        raw=bytes(raw),
    )


def encode_report(targets: tuple[Target, ...] | list[Target], alarm: bool | None = None) -> bytes:
    """Build a complete data frame, as the radar would send it. Used by simulators and tests."""
    if not targets:
        payload = b""
    else:
        if alarm is None:
            alarm = any(t.approaching for t in targets)
        payload = bytes((len(targets), 0x01 if alarm else 0x00))
        payload += b"".join(encode_target(t) for t in targets)
    return DATA_HEADER + struct.pack("<H", len(payload)) + payload + DATA_FOOTER


def encode_params(params: DetectionParams) -> bytes:
    return bytes(
        (
            params.max_distance_m,
            int(params.direction),
            params.min_speed_kmh,
            params.no_target_delay_s,
        )
    )


def decode_params(data: bytes) -> DetectionParams:
    if len(data) < 4:
        raise FrameError(f"detection parameters need 4 bytes, got {len(data)}")
    try:
        return DetectionParams(
            max_distance_m=data[0],
            direction=Direction(data[1]),
            min_speed_kmh=data[2],
            no_target_delay_s=data[3],
        )
    except ValueError as exc:
        raise FrameError(f"invalid detection parameters {data[:4].hex(' ')}: {exc}") from exc


def encode_sensitivity(sens: Sensitivity) -> bytes:
    return bytes((sens.trigger_count, sens.snr_threshold, sens.extended1, sens.extended2))


def decode_sensitivity(data: bytes) -> Sensitivity:
    if len(data) < 4:
        raise FrameError(f"sensitivity needs 4 bytes, got {len(data)}")
    try:
        return Sensitivity(
            trigger_count=data[0], snr_threshold=data[1], extended1=data[2], extended2=data[3]
        )
    except ValueError as exc:
        raise FrameError(f"invalid sensitivity {data[:4].hex(' ')}: {exc}") from exc


def decode_firmware(data: bytes) -> FirmwareVersion:
    if len(data) < 8:
        raise FrameError(f"firmware version needs 8 bytes, got {len(data)}")
    fw_type, major, minor = struct.unpack_from("<HHI", data)
    return FirmwareVersion(firmware_type=fw_type, major=major, minor=minor)


def decode_config_info(data: bytes) -> ConfigSessionInfo:
    if len(data) < 4:
        raise FrameError(f"enable-configuration ACK needs 4 data bytes, got {len(data)}")
    version, buffer_size = struct.unpack_from("<HH", data)
    return ConfigSessionInfo(protocol_version=version, buffer_size=buffer_size)


def encode_baudrate(baudrate: int) -> bytes:
    try:
        return struct.pack("<H", BAUDRATE_INDEX[baudrate])
    except KeyError:
        supported = ", ".join(str(b) for b in BAUDRATE_INDEX)
        raise ValueError(f"unsupported baud rate {baudrate}; supported: {supported}") from None
