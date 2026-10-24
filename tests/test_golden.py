"""Every hex example from the manual (docs/protocol.md), encoded and decoded."""

from __future__ import annotations

import pytest

from ld2451 import DetectionParams, Direction, FrameDecoder, FrameKind, Sensitivity, Target
from ld2451.protocol import (
    CommandCode,
    decode_ack,
    decode_config_info,
    decode_firmware,
    decode_params,
    decode_report,
    decode_sensitivity,
    encode_ack,
    encode_baudrate,
    encode_command,
    encode_params,
    encode_report,
    encode_sensitivity,
)

from .conftest import h

# (command, value, send frame, ACK frame) - manual pages 3-8.
COMMAND_EXAMPLES = [
    (
        CommandCode.ENABLE_CONFIG,
        "01 00",
        "FD FC FB FA 04 00 FF 00 01 00 04 03 02 01",
        "FD FC FB FA 08 00 FF 01 00 00 01 00 00 00 04 03 02 01",
    ),
    (
        CommandCode.END_CONFIG,
        "",
        "FD FC FB FA 02 00 FE 00 04 03 02 01",
        "FD FC FB FA 04 00 FE 01 00 00 04 03 02 01",
    ),
    (
        CommandCode.SET_PARAMS,
        "64 01 05 02",
        "FD FC FB FA 06 00 02 00 64 01 05 02 04 03 02 01",
        "FD FC FB FA 04 00 02 01 00 00 04 03 02 01",
    ),
    (
        CommandCode.GET_PARAMS,
        "",
        "FD FC FB FA 02 00 12 00 04 03 02 01",
        "FD FC FB FA 08 00 12 01 00 00 64 01 05 02 04 03 02 01",
    ),
    (
        CommandCode.SET_SENSITIVITY,
        "02 08 00 00",
        "FD FC FB FA 06 00 03 00 02 08 00 00 04 03 02 01",
        "FD FC FB FA 04 00 03 01 00 00 04 03 02 01",
    ),
    (
        CommandCode.GET_SENSITIVITY,
        "",
        "FD FC FB FA 02 00 13 00 04 03 02 01",
        "FD FC FB FA 08 00 13 01 00 00 01 00 00 00 04 03 02 01",
    ),
    (
        CommandCode.GET_FIRMWARE,
        "",
        "FD FC FB FA 02 00 A0 00 04 03 02 01",
        # The manual prints length 0B 00, but 12 body bytes follow (docs/protocol.md, item 3).
        "FD FC FB FA 0C 00 A0 01 00 00 51 24 01 01 10 15 05 24 04 03 02 01",
    ),
    (
        CommandCode.SET_BAUDRATE,
        "07 00",
        "FD FC FB FA 04 00 A1 00 07 00 04 03 02 01",
        "FD FC FB FA 04 00 A1 01 00 00 04 03 02 01",
    ),
    (
        CommandCode.FACTORY_RESET,
        "",
        "FD FC FB FA 02 00 A2 00 04 03 02 01",
        "FD FC FB FA 04 00 A2 01 00 00 04 03 02 01",
    ),
    (
        CommandCode.RESTART,
        "",
        "FD FC FB FA 02 00 A3 00 04 03 02 01",
        "FD FC FB FA 04 00 A3 01 00 00 04 03 02 01",
    ),
]

REPORT_EXAMPLE = "F4 F3 F2 F1 11 00 03 01 8A 28 00 3C 15 8A 1E 01 3C 0F 76 5F 00 3C 0F F8 F7 F6 F5"
REPORT_TARGETS = (
    Target(angle_deg=10, distance_m=40, approaching=True, speed_kmh=60, snr=0x15),
    Target(angle_deg=10, distance_m=30, approaching=False, speed_kmh=60, snr=0x0F),
    Target(angle_deg=-10, distance_m=95, approaching=True, speed_kmh=60, snr=0x0F),
)


def _decode_one(frame: bytes, kind: FrameKind) -> bytes:
    frames = FrameDecoder().feed(frame, 0)
    assert len(frames) == 1
    assert frames[0].kind is kind
    assert frames[0].raw == frame
    return frames[0].payload


@pytest.mark.parametrize(("code", "value", "send", "ack"), COMMAND_EXAMPLES)
def test_command_examples(code: CommandCode, value: str, send: str, ack: str) -> None:
    assert encode_command(code, h(value)) == h(send)
    decoded = decode_ack(_decode_one(h(ack), FrameKind.COMMAND))
    assert decoded.code == code
    assert decoded.ok
    assert encode_ack(code, 0, decoded.data) == h(ack)


def test_enable_config_ack_data() -> None:
    ack = decode_ack(_decode_one(h(COMMAND_EXAMPLES[0][3]), FrameKind.COMMAND))
    info = decode_config_info(ack.data)
    assert info.protocol_version == 1
    assert info.buffer_size == 0


def test_params_example() -> None:
    params = DetectionParams(
        max_distance_m=100, direction=Direction.APPROACH_ONLY, min_speed_kmh=5, no_target_delay_s=2
    )
    assert encode_params(params) == h("64 01 05 02")
    ack = decode_ack(_decode_one(h(COMMAND_EXAMPLES[3][3]), FrameKind.COMMAND))
    assert decode_params(ack.data) == params


def test_sensitivity_examples() -> None:
    assert encode_sensitivity(Sensitivity(trigger_count=2, snr_threshold=8)) == h("02 08 00 00")
    ack = decode_ack(_decode_one(h(COMMAND_EXAMPLES[5][3]), FrameKind.COMMAND))
    assert decode_sensitivity(ack.data) == Sensitivity(trigger_count=1, snr_threshold=0)


def test_firmware_example() -> None:
    ack = decode_ack(_decode_one(h(COMMAND_EXAMPLES[6][3]), FrameKind.COMMAND))
    fw = decode_firmware(ack.data)
    assert fw.firmware_type == 0x2451
    assert fw.version == "V1.01.24051510"


def test_baudrate_example() -> None:
    assert encode_baudrate(256000) == h("07 00")
    assert encode_baudrate(115200) == h("05 00")


def test_report_example() -> None:
    frame = h(REPORT_EXAMPLE)
    report = decode_report(_decode_one(frame, FrameKind.DATA), 123, frame)
    assert report.targets == REPORT_TARGETS
    assert report.reported_count == 3
    assert report.approach_alarm
    assert report.t_rx_ns == 123
    assert report.raw == frame
    assert encode_report(REPORT_TARGETS) == frame


def test_empty_report_example() -> None:
    frame = h("F4 F3 F2 F1 00 00 F8 F7 F6 F5")
    report = decode_report(_decode_one(frame, FrameKind.DATA), 0)
    assert report.targets == ()
    assert report.reported_count == 0
    assert not report.approach_alarm
    assert encode_report(()) == frame
