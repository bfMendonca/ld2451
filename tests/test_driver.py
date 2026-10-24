from __future__ import annotations

import threading
import time

import pytest

from ld2451 import (
    LD2451,
    CommandRejected,
    CommandTimeout,
    ConfigSessionError,
    DetectionParams,
    Direction,
    LoopbackTransport,
    RadarReport,
    Sensitivity,
    UnverifiedCommandWarning,
    VerificationError,
)
from ld2451.protocol import CommandCode, encode_ack, encode_report

from .conftest import FakeRadar, h
from .test_golden import REPORT_EXAMPLE, REPORT_TARGETS

PARAMS = DetectionParams(
    max_distance_m=30, direction=Direction.BOTH, min_speed_kmh=3, no_target_delay_s=1
)


def test_reports_queue_and_listener(radar: LD2451, loopback: LoopbackTransport) -> None:
    seen: list[RadarReport] = []
    radar.add_listener(seen.append)
    loopback.inject(h(REPORT_EXAMPLE) + h("F4 F3 F2 F1 00 00 F8 F7 F6 F5"))
    first = radar.get_report(timeout=1)
    second = radar.get_report(timeout=1)
    assert first is not None and first.targets == REPORT_TARGETS
    assert second is not None and second.targets == ()
    assert len(seen) == 2
    assert radar.get_report(timeout=0.05) is None
    assert radar.stats.data_frames == 2
    radar.remove_listener(seen.append)


def test_injected_clock_timestamps(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, clock=lambda: 42)
    with radar:
        loopback.inject(h(REPORT_EXAMPLE))
        report = radar.get_report(timeout=1)
    assert report is not None and report.t_rx_ns == 42


def test_default_clock_name(loopback: LoopbackTransport) -> None:
    assert LD2451(loopback).clock_name in ("CLOCK_BOOTTIME", "monotonic")


def test_reports_iterator_timeout(radar: LD2451, loopback: LoopbackTransport) -> None:
    loopback.inject(h(REPORT_EXAMPLE) * 3)
    assert len(list(radar.reports(timeout=0.2))) == 3


def test_reports_iterator_ends_after_stop(radar: LD2451, loopback: LoopbackTransport) -> None:
    loopback.inject(h(REPORT_EXAMPLE))
    deadline = time.monotonic() + 1
    while radar.stats.data_frames < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    radar.stop()
    assert len(list(radar.reports())) == 1


def test_queue_drops_oldest(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, queue_size=2)
    with radar:
        frames = [encode_report([t]) for t in REPORT_TARGETS]
        loopback.inject(b"".join(frames))
        deadline = time.monotonic() + 1
        while radar.stats.data_frames < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        got = [radar.get_report(0.1) for _ in range(2)]
    assert [r.targets[0] for r in got if r] == list(REPORT_TARGETS[1:])
    assert radar.stats.dropped_reports == 1


def test_bad_listener_does_not_kill_reader(radar: LD2451, loopback: LoopbackTransport) -> None:
    def boom(_: RadarReport) -> None:
        raise RuntimeError("listener bug")

    def raw_boom(_t: int, _d: bytes) -> None:
        raise RuntimeError("raw listener bug")

    radar.add_listener(boom)
    radar.add_raw_listener(raw_boom)
    loopback.inject(h(REPORT_EXAMPLE))
    assert radar.get_report(timeout=1) is not None
    loopback.inject(h(REPORT_EXAMPLE))
    assert radar.get_report(timeout=1) is not None
    assert radar.running
    assert radar.stats.callback_errors == 4
    radar.remove_raw_listener(raw_boom)


def test_invalid_payload_counted(radar: LD2451, loopback: LoopbackTransport) -> None:
    loopback.inject(h("F4 F3 F2 F1 03 00 01 02 03 F8 F7 F6 F5"))
    loopback.inject(h("FD FC FB FA 02 00 12 00 04 03 02 01"))  # command echo, not an ACK
    loopback.inject(h(REPORT_EXAMPLE))
    assert radar.get_report(timeout=1) is not None
    assert radar.stats.invalid_payloads == 2


def test_start_stop_idempotent(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback)
    radar.stop()
    radar.open()
    radar.start()
    radar.start()
    assert radar.running
    radar.stop()
    radar.stop()
    assert not radar.running
    radar.close()


def test_session_sends_enable_and_end(radar: LD2451, fake: FakeRadar) -> None:
    with radar.config_session() as info:
        assert info is not None and info.protocol_version == 1
        assert radar.in_config_session
        ack = radar.send_command(CommandCode.GET_FIRMWARE)
        assert ack.ok
    assert fake.received == [0xFF, 0xA0, 0xFE]
    assert not fake.in_config
    assert not radar.in_config_session


def test_session_ends_even_on_error(radar: LD2451, fake: FakeRadar) -> None:
    with pytest.raises(RuntimeError), radar.config_session():
        raise RuntimeError("user code failed")
    assert fake.received[-1] == 0xFE
    assert not fake.in_config


def test_session_enable_failure(radar: LD2451, fake: FakeRadar) -> None:
    fake.reject.add(0xFF)
    with pytest.raises(ConfigSessionError, match="enter"), radar.config_session():
        pass
    assert fake.received[-1] == 0xFE


def test_session_end_failure_raises(radar: LD2451, fake: FakeRadar) -> None:
    fake.silent.add(0xFE)
    with pytest.raises(ConfigSessionError, match="leave"), radar.config_session():
        pass
    assert radar.stats.timeouts == 1


def test_nested_session_rejected(radar: LD2451) -> None:
    with radar.config_session(), pytest.raises(ConfigSessionError), radar.config_session():
        pass


def test_session_auto_starts_reader(loopback: LoopbackTransport, fake: FakeRadar) -> None:
    radar = LD2451(loopback, command_timeout=0.2)
    radar.open()
    try:
        assert radar.get_firmware().version == "V1.01.24051510"
    finally:
        radar.close()


def test_send_command_needs_session(radar: LD2451) -> None:
    with pytest.raises(ConfigSessionError):
        radar.send_command(CommandCode.GET_PARAMS)


def test_send_command_from_other_thread_needs_own_session(radar: LD2451) -> None:
    errors: list[BaseException] = []

    def other() -> None:
        try:
            radar.send_command(CommandCode.GET_PARAMS)
        except BaseException as exc:
            errors.append(exc)

    with radar.config_session():
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert isinstance(errors[0], ConfigSessionError)


def test_unknown_command_warns(radar: LD2451, fake: FakeRadar) -> None:
    with (
        radar.config_session(),
        pytest.warns(UnverifiedCommandWarning),
        pytest.raises(CommandRejected),
    ):
        radar.send_command(0x00A4)


def test_rejected_command(radar: LD2451, fake: FakeRadar) -> None:
    fake.reject.add(0x02)
    with pytest.raises(CommandRejected) as info:
        radar.set_params(PARAMS)
    assert info.value.code == 0x02
    assert info.value.status == 1
    assert not fake.in_config


def test_timeout_with_retries(radar: LD2451, fake: FakeRadar) -> None:
    fake.silent.add(0x12)
    with pytest.raises(CommandTimeout):
        radar.get_params()
    assert fake.received.count(0x12) == 2  # one try + one retry
    assert radar.stats.timeouts == 1


def test_retry_recovers_lost_ack(radar: LD2451, fake: FakeRadar) -> None:
    fake.drop_next[0x12] = 1
    assert radar.get_params().max_distance_m == 100
    assert fake.received.count(0x12) == 2


def test_out_of_order_ack_ignored(
    radar: LD2451, loopback: LoopbackTransport, fake: FakeRadar
) -> None:
    original = loopback.responder
    assert original is not None

    def responder(data: bytes) -> bytes | None:
        reply = original(data)
        if fake.received and fake.received[-1] == 0x12 and reply:
            return encode_ack(0x13, 0, h("01 00 00 00")) + h(REPORT_EXAMPLE) + reply
        return reply

    loopback.responder = responder
    assert radar.get_params().max_distance_m == 100
    assert radar.get_report(timeout=1) is not None  # data during ACK wait still delivered


def test_set_and_get_params(radar: LD2451, fake: FakeRadar) -> None:
    radar.set_params(PARAMS)
    assert fake.params == h("1E 02 03 01")
    assert radar.get_params() == PARAMS


def test_set_params_verification_failure(
    radar: LD2451, loopback: LoopbackTransport, fake: FakeRadar
) -> None:
    original = fake.handle

    def stubborn(code: int, value: bytes) -> bytes | None:
        if code == CommandCode.SET_PARAMS:
            fake.received.append(code)
            return encode_ack(code)  # pretend success but keep the old value
        return original(code, value)

    fake.handle = stubborn  # type: ignore[method-assign]
    with pytest.raises(VerificationError):
        radar.set_params(PARAMS)
    radar.set_params(PARAMS, verify=False)


def test_sensitivity_roundtrip(radar: LD2451, fake: FakeRadar) -> None:
    sens = Sensitivity(trigger_count=2, snr_threshold=8)
    radar.set_sensitivity(sens)
    assert fake.sensitivity == h("02 08 00 00")
    assert radar.get_sensitivity() == sens


def test_sensitivity_verification_failure(radar: LD2451, fake: FakeRadar) -> None:
    original = fake.handle

    def stubborn(code: int, value: bytes) -> bytes | None:
        if code == CommandCode.SET_SENSITIVITY:
            return encode_ack(code)
        return original(code, value)

    fake.handle = stubborn  # type: ignore[method-assign]
    with pytest.raises(VerificationError):
        radar.set_sensitivity(Sensitivity(trigger_count=3, snr_threshold=5))


def test_several_commands_in_one_session(radar: LD2451, fake: FakeRadar) -> None:
    with radar.config_session():
        radar.get_firmware()
        radar.get_params()
        radar.get_sensitivity()
    assert fake.received == [0xFF, 0xA0, 0x12, 0x13, 0xFE]


def test_destructive_commands_need_confirm(radar: LD2451, fake: FakeRadar) -> None:
    with pytest.raises(ValueError):
        radar.set_baudrate(256000)
    with pytest.raises(ValueError):
        radar.factory_reset()
    with pytest.raises(ValueError):
        radar.set_baudrate(1234, confirm=True)
    assert fake.received == []
    radar.set_baudrate(256000, confirm=True)
    assert fake.baud_index == 7
    radar.factory_reset(confirm=True)
    assert 0xA2 in fake.received


def test_restart_skips_end_config(radar: LD2451, fake: FakeRadar) -> None:
    radar.restart()
    assert fake.restarted
    assert fake.received == [0xFF, 0xA3]


def test_command_without_reader_fails(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback)
    radar.open()
    with pytest.raises(ConfigSessionError):
        radar._transact(CommandCode.GET_PARAMS)


def test_reconnects_after_transport_error(radar: LD2451, loopback: LoopbackTransport) -> None:
    loopback.fail_reads = 2
    deadline = time.monotonic() + 2
    while radar.stats.reconnects < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert radar.stats.reconnects == 2
    loopback.inject(h(REPORT_EXAMPLE))
    assert radar.get_report(timeout=1) is not None


def test_reconnect_retries_open_with_backoff(loopback: LoopbackTransport) -> None:
    from ld2451 import TransportError

    opens = {"n": 0}
    real_open = loopback.open

    def flaky_open() -> None:
        opens["n"] += 1
        if 1 < opens["n"] <= 3:
            raise TransportError("port gone")
        real_open()

    loopback.open = flaky_open  # type: ignore[method-assign]
    radar = LD2451(loopback, reconnect_delay=0.01, reconnect_max_delay=0.02)
    with radar:
        loopback.fail_reads = 1
        deadline = time.monotonic() + 2
        while radar.stats.reconnects < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert radar.stats.reconnects == 1
    assert opens["n"] == 4


def test_no_reconnect_stops_reader(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, reconnect=False)
    with radar:
        loopback.fail_reads = 1
        deadline = time.monotonic() + 1
        while radar.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not radar.running
        assert radar.last_error is not None


def test_stop_during_reconnect_wait(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, reconnect_delay=5)
    with radar:
        loopback.fail_reads = 1
        time.sleep(0.05)
        t0 = time.monotonic()
        radar.stop()
        assert time.monotonic() - t0 < 1
        assert not radar.running


def test_multiple_instances_coexist() -> None:
    fakes = [FakeRadar(), FakeRadar()]
    fakes[1].firmware = h("51 24 02 01 01 00 00 25")
    radars = [LD2451(LoopbackTransport(responder=f, timeout=0.01)) for f in fakes]
    for r in radars:
        r.open()
    try:
        assert [r.get_firmware().version for r in radars] == ["V1.01.24051510", "V1.02.25000001"]
    finally:
        for r in radars:
            r.close()


# ---- regressions from code review ----------------------------------------------------------


def test_replay_mode_keeps_every_report_with_slow_consumer(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, queue_size=4, drop_oldest=False)
    frame = h(REPORT_EXAMPLE)
    with radar:
        loopback.inject(frame * 50)
        got = 0
        while radar.get_report(timeout=0.5) is not None:
            got += 1
            time.sleep(0.001)
    assert got == 50
    assert radar.stats.dropped_reports == 0


def test_blocked_reader_still_stops(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, queue_size=1, drop_oldest=False)
    with radar:
        loopback.inject(h(REPORT_EXAMPLE) * 5)
        time.sleep(0.1)  # reader now blocked on the full queue
        t0 = time.monotonic()
        radar.stop()
        assert time.monotonic() - t0 < 1
        assert not radar.running


def test_command_from_listener_fails_fast(radar: LD2451, loopback: LoopbackTransport) -> None:
    errors: list[BaseException] = []

    def listener(_: RadarReport) -> None:
        try:
            radar.get_params()
        except BaseException as exc:
            errors.append(exc)

    radar.add_listener(listener)
    t0 = time.monotonic()
    loopback.inject(h(REPORT_EXAMPLE))
    assert radar.get_report(timeout=1) is not None
    deadline = time.monotonic() + 1
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    assert isinstance(errors[0], ConfigSessionError)
    assert "listener" in str(errors[0])
    assert time.monotonic() - t0 < 0.5


def test_stop_and_close_from_listener(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback)
    closed = threading.Event()

    def listener(_: RadarReport) -> None:
        radar.close()
        closed.set()

    radar.add_listener(listener)
    with radar:
        loopback.inject(h(REPORT_EXAMPLE))
        assert closed.wait(1)
        deadline = time.monotonic() + 1
        while radar.running and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not radar.running
    assert not loopback.is_open
    assert radar.stats.callback_errors == 0


def test_restart_is_not_retried(radar: LD2451, fake: FakeRadar) -> None:
    fake.drop_next[0xA3] = 1
    with pytest.raises(CommandTimeout):
        radar.restart()
    assert fake.received.count(0xA3) == 1


def test_backoff_grows_across_immediate_failures(loopback: LoopbackTransport) -> None:
    radar = LD2451(loopback, reconnect_delay=0.01, reconnect_max_delay=0.04)
    waits: list[float] = []
    real_wait = radar._stop.wait

    def recording_wait(timeout: float | None = None) -> bool:
        if timeout is not None:
            waits.append(timeout)
        return real_wait(timeout)

    radar._stop.wait = recording_wait  # type: ignore[method-assign]
    with radar:
        loopback.fail_reads = 4  # every reopen succeeds, the next read fails again
        deadline = time.monotonic() + 2
        while radar.stats.reconnects < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert waits[:4] == [0.01, 0.02, 0.04, 0.04]
        loopback.inject(h(REPORT_EXAMPLE))
        assert radar.get_report(timeout=1) is not None
        loopback.fail_reads = 1
        deadline = time.monotonic() + 1
        while radar.stats.reconnects < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert waits[4] == 0.01  # reset after a successful read
