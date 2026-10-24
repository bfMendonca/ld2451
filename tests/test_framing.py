from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ld2451 import FrameDecoder, FrameKind, Target
from ld2451.protocol import encode_ack, encode_command, encode_report

from .conftest import h
from .test_golden import REPORT_EXAMPLE

REPORT = h(REPORT_EXAMPLE)
EMPTY = h("F4 F3 F2 F1 00 00 F8 F7 F6 F5")
ACK = encode_ack(0x00FF, 0, h("01 00 00 00"))


def test_whole_frame() -> None:
    d = FrameDecoder()
    frames = d.feed(REPORT, 7)
    assert [f.raw for f in frames] == [REPORT]
    assert frames[0].t_rx_ns == 7
    assert d.buffered == 0
    assert d.bytes_discarded == 0
    assert d.data_frames == 1


def test_byte_by_byte_keeps_first_byte_time() -> None:
    d = FrameDecoder()
    out = []
    for i, b in enumerate(REPORT):
        out += d.feed(bytes([b]), 1000 + i)
    assert len(out) == 1
    assert out[0].raw == REPORT
    assert out[0].t_rx_ns == 1000


def test_concatenated_frames_mixed_kinds() -> None:
    d = FrameDecoder()
    frames = d.feed(REPORT + ACK + EMPTY + REPORT, 5)
    assert [f.kind for f in frames] == [
        FrameKind.DATA,
        FrameKind.COMMAND,
        FrameKind.DATA,
        FrameKind.DATA,
    ]
    assert d.command_frames == 1
    assert d.data_frames == 3


def test_garbage_before_and_between() -> None:
    d = FrameDecoder()
    frames = d.feed(b"\x00\x11\xf4\xf3" + REPORT + b"\xff\xfd" + EMPTY, 0)
    assert [f.raw for f in frames] == [REPORT, EMPTY]
    assert d.bytes_discarded == 6


def test_timestamp_per_frame_across_chunks() -> None:
    d = FrameDecoder()
    assert d.feed(REPORT[:5], 1) == []
    frames = d.feed(REPORT[5:] + EMPTY[:3], 2)
    assert [f.t_rx_ns for f in frames] == [1]
    frames = d.feed(EMPTY[3:], 3)
    assert [f.t_rx_ns for f in frames] == [2]


def test_bad_length_resyncs() -> None:
    d = FrameDecoder()
    bad = h("F4 F3 F2 F1 FF 00")  # 255 > 27
    frames = d.feed(bad + REPORT, 0)
    assert [f.raw for f in frames] == [REPORT]
    assert d.bad_lengths == 1


def test_bad_footer_resyncs() -> None:
    d = FrameDecoder()
    corrupt = bytearray(REPORT)
    corrupt[-1] ^= 0xFF
    frames = d.feed(bytes(corrupt) + EMPTY, 0)
    assert [f.raw for f in frames] == [EMPTY]
    assert d.bad_footers == 1


def test_frame_hidden_inside_corrupt_frame_is_recovered() -> None:
    # A truncated frame whose declared length swallows the next real frame.
    d = FrameDecoder()
    truncated = REPORT[:10]
    frames = d.feed(truncated + EMPTY + EMPTY, 0)
    assert EMPTY in [f.raw for f in frames]
    assert d.bad_footers >= 1


def test_partial_header_tail_kept() -> None:
    d = FrameDecoder()
    assert d.feed(b"\x01\x02\x03\xf4\xf3", 0) == []
    assert d.buffered == 2
    assert d.feed(REPORT[2:], 0)[0].raw == REPORT


def test_reset_and_max_buffer_validation() -> None:
    d = FrameDecoder()
    d.feed(REPORT[:8], 0)
    d.reset()
    assert d.buffered == 0
    assert d.bytes_discarded == 8
    with pytest.raises(ValueError):
        FrameDecoder(max_buffer=10)


def test_command_frames_decoded_too() -> None:
    frame = encode_command(0x0012)
    assert FrameDecoder().feed(frame, 0)[0].kind is FrameKind.COMMAND


targets = st.builds(
    Target,
    angle_deg=st.integers(-128, 127),
    distance_m=st.integers(0, 255),
    approaching=st.booleans(),
    speed_kmh=st.integers(0, 255),
    snr=st.integers(0, 255),
)
valid_frames = st.one_of(
    st.lists(targets, max_size=5).map(encode_report),
    st.binary(max_size=40).map(lambda v: encode_ack(0x12, 0, v)),
)


@settings(max_examples=300)
@given(st.lists(st.binary(max_size=64), max_size=20))
def test_arbitrary_bytes_never_raise(chunks: list[bytes]) -> None:
    d = FrameDecoder(max_buffer=200)
    for i, c in enumerate(chunks):
        for f in d.feed(c, i):
            assert f.raw[6 : 6 + len(f.payload)] == f.payload
        assert d.buffered <= 200


@settings(max_examples=300)
@given(
    st.lists(st.tuples(st.binary(max_size=30), valid_frames), min_size=1, max_size=8),
    st.integers(1, 17),
)
def test_valid_frames_among_garbage_are_recovered(
    pieces: list[tuple[bytes, bytes]], chunk: int
) -> None:
    # Garbage must not contain header bytes, or it could legitimately start a longer frame
    # that swallows the real one; that case is covered by the resync tests above.
    stream = b""
    expected = []
    for garbage, frame in pieces:
        clean = bytes(b for b in garbage if b not in (0xF4, 0xFD))
        stream += clean + frame
        expected.append(frame)
    d = FrameDecoder()
    got = []
    for i in range(0, len(stream), chunk):
        got += [f.raw for f in d.feed(stream[i : i + chunk], i)]
    assert got == expected
