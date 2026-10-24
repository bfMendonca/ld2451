from __future__ import annotations

import pytest

from ld2451 import DetectionParams, Direction, FirmwareVersion, FrameError, Sensitivity, Target
from ld2451.models import RadarReport
from ld2451.protocol import (
    decode_ack,
    decode_command,
    decode_config_info,
    decode_firmware,
    decode_params,
    decode_report,
    decode_sensitivity,
    decode_target,
    encode_baudrate,
    encode_command,
)

from .conftest import h


def test_decode_ack_failure_status() -> None:
    ack = decode_ack(h("02 01 01 00"))
    assert ack.code == 0x0002
    assert not ack.ok
    assert ack.status == 1


@pytest.mark.parametrize("payload", ["", "02 01", "02 00 00 00"])
def test_decode_ack_rejects(payload: str) -> None:
    with pytest.raises(FrameError):
        decode_ack(h(payload))


def test_decode_command_roundtrip() -> None:
    frame = encode_command(0x0002, h("64 01 05 02"))
    assert decode_command(frame[6:-4]) == (0x0002, h("64 01 05 02"))
    with pytest.raises(FrameError):
        decode_command(b"\x01")


def test_encode_command_limits() -> None:
    with pytest.raises(ValueError):
        encode_command(0x10000)
    with pytest.raises(ValueError):
        encode_command(0x01, bytes(63))


@pytest.mark.parametrize("length", [1, 3, 8, 32])
def test_decode_report_bad_lengths(length: int) -> None:
    with pytest.raises(FrameError):
        decode_report(bytes(length), 0)


def test_decode_report_ignores_count_byte() -> None:
    # Count byte says 5 but only one target follows: the length wins.
    report = decode_report(h("05 00 80 0A 01 05 10"), 0)
    assert len(report.targets) == 1
    assert report.reported_count == 5
    assert not report.approach_alarm


def test_decode_target_bad_direction() -> None:
    with pytest.raises(FrameError):
        decode_target(h("80 0A 02 05 10"))
    with pytest.raises(FrameError):
        decode_target(h("80 0A"))


def test_decode_target_angle_extremes() -> None:
    assert decode_target(h("00 00 00 00 00")).angle_deg == -128
    assert decode_target(h("FF 00 01 00 00")).angle_deg == 127


@pytest.mark.parametrize(
    ("fn", "data"),
    [
        (decode_params, "64 01 05"),
        (decode_params, "05 01 05 02"),  # distance below 10 m
        (decode_params, "64 07 05 02"),  # unknown direction
        (decode_sensitivity, "01 00"),
        (decode_sensitivity, "00 00 00 00"),  # trigger count 0
        (decode_firmware, "51 24 01 01"),
        (decode_config_info, "01 00"),
    ],
)
def test_decoders_reject_bad_data(fn: object, data: str) -> None:
    with pytest.raises(FrameError):
        fn(h(data))  # type: ignore[operator]


def test_encode_baudrate_unknown() -> None:
    with pytest.raises(ValueError, match="supported"):
        encode_baudrate(12345)


def test_model_validation() -> None:
    with pytest.raises(ValueError):
        Target(angle_deg=200, distance_m=0, approaching=True, speed_kmh=0, snr=0)
    with pytest.raises(ValueError):
        DetectionParams(
            max_distance_m=100, direction=Direction.BOTH, min_speed_kmh=121, no_target_delay_s=0
        )
    with pytest.raises(ValueError):
        DetectionParams(max_distance_m=100, direction=9, min_speed_kmh=0, no_target_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Sensitivity(trigger_count=1, snr_threshold=2)
    with pytest.raises(ValueError):
        FirmwareVersion(firmware_type=0, major=0x10000, minor=0)
    t = Target(angle_deg=0, distance_m=0, approaching=True, speed_kmh=0, snr=0)
    with pytest.raises(ValueError):
        RadarReport(t_rx_ns=0, targets=(t,) * 6, header_bytes=b"", raw=b"")
    with pytest.raises(ValueError):
        RadarReport(t_rx_ns=0, targets=(), header_bytes=b"\x00", raw=b"")
