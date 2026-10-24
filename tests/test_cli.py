from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ld2451 import LD2451, LoopbackTransport, RadarReport, cli

from .conftest import FakeRadar, h
from .test_golden import REPORT_EXAMPLE

FIXTURE = Path(__file__).parent / "fixtures" / "manual_examples.csv"


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch, fake: FakeRadar) -> LoopbackTransport:
    transport = LoopbackTransport(responder=fake, timeout=0.01)

    def make(_args: Any) -> LD2451:
        return LD2451(transport, command_timeout=0.2, command_retries=0)

    monkeypatch.setattr(cli, "_radar", make)
    return transport


def test_replay_prints_targets(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["replay", str(FIXTURE), "--stats"]) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert len(lines) == 4
    assert lines[1] == (
        "1.100000 targets=3 [+10deg 40m approaching 60km/h snr=21] "
        "[+10deg 30m receding 60km/h snr=15] [-10deg 95m approaching 60km/h snr=15]"
    )
    assert "bytes_discarded=3" in out.err


def test_replay_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["replay", str(FIXTURE), "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["alarm"] is True
    assert rows[1]["targets"][2]["angle_deg"] == -10


def test_replay_missing_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert cli.main(["replay", str(tmp_path / "x.csv")]) == 1
    assert "cannot open replay" in capsys.readouterr().err


def test_monitor(fake_serial: LoopbackTransport, capsys: pytest.CaptureFixture[str]) -> None:
    fake_serial.inject(h(REPORT_EXAMPLE) * 2)
    assert cli.main(["monitor", "--count", "2", "--stats"]) == 0
    out = capsys.readouterr()
    assert len(out.out.splitlines()) == 2
    assert "data_frames=2" in out.err


def test_info(fake_serial: LoopbackTransport, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "firmware: V1.01.24051510 (type 0x2451)" in out
    assert "max_distance=100m direction=approach_only" in out
    assert "trigger_count=1 snr_threshold=0" in out


def test_config_merges_with_current(
    fake_serial: LoopbackTransport, fake: FakeRadar, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["config", "--max-distance", "30", "--direction", "both"]) == 0
    assert fake.params == h("1E 02 05 02")
    assert cli.main(["config", "--trigger-count", "3", "--snr-threshold", "5"]) == 0
    assert fake.sensitivity == h("03 05 00 00")
    assert fake.received.count(0xFE) == 2


def test_config_destructive_needs_yes(
    fake_serial: LoopbackTransport, fake: FakeRadar, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["config", "--set-baudrate", "256000"]) == 2
    assert cli.main(["config"]) == 2
    assert fake.received == []
    assert cli.main(["config", "--set-baudrate", "256000", "--yes", "--restart"]) == 0
    assert fake.baud_index == 7
    assert fake.restarted
    assert cli.main(["config", "--factory-reset", "--yes"]) == 0
    assert 0xA2 in fake.received


def test_config_error_exit_code(
    fake_serial: LoopbackTransport, fake: FakeRadar, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.reject.add(0x02)
    assert cli.main(["config", "--min-speed", "3"]) == 1
    assert "rejected" in capsys.readouterr().err


def test_record(
    fake_serial: LoopbackTransport, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "rec.csv"
    fake_serial.inject(h(REPORT_EXAMPLE))
    assert cli.main(["record", str(path), "--duration", "0.3", "--show"]) == 0
    assert cli.main(["replay", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].split()[1:] == lines[1].split()[1:]


def test_import_has_no_side_effects() -> None:
    code = (
        "import logging, threading, sys; import ld2451; "
        "assert threading.active_count() == 1, threading.enumerate(); "
        "assert not logging.getLogger().handlers; "
        "assert 'serial.tools' not in sys.modules; "
        "assert any(isinstance(h, logging.NullHandler) "
        "for h in logging.getLogger('ld2451').handlers); "
        "assert set(ld2451.__all__) <= set(dir(ld2451))"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_replay_large_log_prints_every_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "big.csv"
    frame = h(REPORT_EXAMPLE).hex()
    path.write_text("".join(f"{i * 1000},{frame}\n" for i in range(2000)))
    assert cli.main(["replay", str(path), "--stats"]) == 0
    out = capsys.readouterr()
    assert len(out.out.splitlines()) == 2000
    assert "dropped_reports=0" in out.err


def test_monitor_stats_printed_on_ctrl_c(
    fake_serial: LoopbackTransport,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted(self: LD2451, timeout: float | None = None) -> Iterator[RadarReport]:
        raise KeyboardInterrupt
        yield  # pragma: no cover

    monkeypatch.setattr(LD2451, "reports", interrupted)
    assert cli.main(["monitor", "--stats"]) == 130
    assert "Stats(" in capsys.readouterr().err


def test_config_rejects_factory_reset_with_writes(
    fake_serial: LoopbackTransport, fake: FakeRadar, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["config", "--factory-reset", "--max-distance", "30", "--yes"]) == 2
    assert fake.received == []


@pytest.mark.parametrize(
    "args",
    [
        ["--max-distance", "5"],
        ["--min-speed", "121"],
        ["--delay", "256"],
        ["--trigger-count", "0"],
        ["--snr-threshold", "2"],
    ],
)
def test_config_validates_before_opening_port(
    fake_serial: LoopbackTransport, fake: FakeRadar, args: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["config", *args])
    assert info.value.code == 2
    assert fake.received == []
