from __future__ import annotations

from pathlib import Path

import pytest

from ld2451 import LD2451, Recorder, ReplayTransport, TransportError, read_records
from ld2451.recorder import parse_line

from .test_golden import REPORT_TARGETS

FIXTURE = Path(__file__).parent / "fixtures" / "manual_examples.csv"


def test_recorder_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    with Recorder(path) as rec:
        rec.write(1, b"\x01\x02")
        rec.write(2, b"")
        rec.write(3, b"\xff")
        rec.flush()
    assert path.read_text().splitlines()[0] == "t_rx_ns,hex"
    assert list(read_records(path)) == [(1, b"\x01\x02"), (3, b"\xff")]
    with pytest.raises(ValueError):
        rec.write(4, b"\x00")


def test_parse_line() -> None:
    assert parse_line("") is None
    assert parse_line("# comment") is None
    assert parse_line("t_rx_ns,hex") is None
    assert parse_line("5,0aff") == (5, b"\x0a\xff")
    with pytest.raises(ValueError):
        parse_line("5,zz")


def test_read_records_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("1,00\n2,xyz\n")
    with pytest.raises(ValueError, match=":2:"):
        list(read_records(path))


def test_replay_fixture_keeps_recorded_timestamps() -> None:
    radar = LD2451(ReplayTransport(FIXTURE), reconnect=False)
    with radar:
        reports = list(radar.reports(timeout=0.3))
    assert [len(r.targets) for r in reports] == [0, 3, 1, 0]
    assert [r.t_rx_ns for r in reports] == [1000000000, 1100000000, 1200000000, 1300000000]
    assert reports[1].targets == REPORT_TARGETS
    t = reports[2].targets[0]
    assert (t.angle_deg, t.distance_m, t.approaching, t.speed_kmh, t.snr) == (10, 20, True, 50, 6)
    assert radar.stats.bytes_discarded == 3


def test_replay_splits_large_chunks_and_drops_writes(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("7,0102030405\n")
    rt = ReplayTransport(path, timeout=0.001)
    with pytest.raises(TransportError):
        rt.read(1)
    rt.open()
    rt.open()
    assert rt.write(b"abc") == 3
    rt.flush_input()
    assert rt.read_timestamped(2) == (b"\x01\x02", 7)
    assert rt.read(10) == b"\x03\x04\x05"
    assert not rt.exhausted
    assert rt.read(10) == b""
    assert rt.exhausted
    rt.close()


def test_replay_realtime_paces(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("0,01\n30000000,02\n")
    rt = ReplayTransport(path, realtime=True, timeout=0.01)
    rt.open()
    assert rt.read(10) == b"\x01"
    got = b""
    for _ in range(20):
        got = rt.read(10)
        if got:
            break
    assert got == b"\x02"


def test_replay_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TransportError):
        ReplayTransport(tmp_path / "nope.csv").open()
    bad = tmp_path / "bad.csv"
    bad.write_text("1,zz\n")
    with pytest.raises(TransportError):
        ReplayTransport(bad).open()


def test_replay_real_idle_capture() -> None:
    path = Path(__file__).parent / "fixtures" / "real_idle.csv"
    records = list(read_records(path))
    radar = LD2451(ReplayTransport(path), reconnect=False)
    with radar:
        reports = list(radar.reports(timeout=0.3))
    assert len(reports) == 7
    assert all(r.targets == () for r in reports)
    # The frame timestamp is the read that carried the first header byte.
    first_byte_times = [t for t, data in records if data.startswith(b"\xf4")]
    assert [r.t_rx_ns for r in reports] == first_byte_times
    gaps = [b.t_rx_ns - a.t_rx_ns for a, b in zip(reports, reports[1:])]
    assert all(1.3e9 < g < 1.5e9 for g in gaps)
    stats = radar.stats
    assert (stats.bytes_discarded, stats.bad_footers, stats.bad_lengths) == (0, 0, 0)
