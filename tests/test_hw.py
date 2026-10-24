"""Hardware tests: run on the Pi with `pytest --hw --port=/dev/serial0 tests/test_hw.py`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ld2451 import LD2451, DetectionParams, Direction, Sensitivity, SerialTransport

pytestmark = pytest.mark.hw


@pytest.fixture
def hw_radar(request: pytest.FixtureRequest) -> Iterator[LD2451]:
    with LD2451(SerialTransport(request.config.getoption("--port"))) as radar:
        yield radar


# With no target the radar sends an empty frame about every 1.4 s.
REPORT_WAIT_S = 3.0


def _drain(radar: LD2451) -> None:
    while radar.get_report(timeout=0) is not None:
        pass


def test_receives_reports(hw_radar: LD2451) -> None:
    reports = [hw_radar.get_report(timeout=REPORT_WAIT_S) for _ in range(3)]
    assert all(r is not None for r in reports), f"fewer than 3 frames ({REPORT_WAIT_S} s each)"
    stats = hw_radar.stats
    assert stats.bad_footers == 0
    assert stats.bad_lengths == 0
    assert stats.invalid_payloads == 0


def test_firmware(hw_radar: LD2451) -> None:
    fw = hw_radar.get_firmware()
    assert fw.firmware_type == 0x2451
    print(fw.version)


def test_params_roundtrip(hw_radar: LD2451) -> None:
    original = hw_radar.get_params()
    trial = DetectionParams(
        max_distance_m=50, direction=Direction.BOTH, min_speed_kmh=2, no_target_delay_s=1
    )
    try:
        hw_radar.set_params(trial)
        assert hw_radar.get_params() == trial
    finally:
        hw_radar.set_params(original)


def test_sensitivity_roundtrip(hw_radar: LD2451) -> None:
    original = hw_radar.get_sensitivity()
    try:
        hw_radar.set_sensitivity(Sensitivity(trigger_count=2, snr_threshold=5))
        assert hw_radar.get_sensitivity() == Sensitivity(trigger_count=2, snr_threshold=5)
    finally:
        hw_radar.set_sensitivity(original)


def test_reports_resume_after_session(hw_radar: LD2451) -> None:
    hw_radar.get_params()
    _drain(hw_radar)
    t_end = hw_radar._clock()
    report = hw_radar.get_report(timeout=REPORT_WAIT_S)
    assert report is not None, "no data after leaving configuration mode"
    assert report.t_rx_ns >= t_end
