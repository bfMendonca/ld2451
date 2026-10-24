"""SerialTransport over a pseudo-terminal, with the fake radar on the other end."""

from __future__ import annotations

import os
import sys
import threading
import tty
from collections.abc import Iterator

import pytest

from ld2451 import LD2451, SerialTransport, TransportError

from .conftest import FakeRadar, h
from .test_golden import REPORT_EXAMPLE, REPORT_TARGETS

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ptys")


class PtyRadar:
    """Serves FakeRadar on the master side of a pty; the slave path acts as the serial port."""

    def __init__(self) -> None:
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self._slave = slave
        self.fake = FakeRadar()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        import select

        while not self._stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 256)
            except OSError:
                return
            reply = self.fake(data)
            if reply:
                os.write(self.master, reply)

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1)
        os.close(self.master)
        os.close(self._slave)


@pytest.fixture
def pty_radar() -> Iterator[PtyRadar]:
    p = PtyRadar()
    yield p
    p.close()


def test_reports_over_serial(pty_radar: PtyRadar) -> None:
    with LD2451(SerialTransport(pty_radar.path)) as radar:
        pty_radar.send(h(REPORT_EXAMPLE))
        report = radar.get_report(timeout=2)
    assert report is not None
    assert report.targets == REPORT_TARGETS


def test_commands_over_serial(pty_radar: PtyRadar) -> None:
    with LD2451(SerialTransport(pty_radar.path)) as radar:
        assert radar.get_firmware().version == "V1.01.24051510"
        assert radar.get_params().max_distance_m == 100
    assert pty_radar.fake.received == [0xFF, 0xA0, 0xFE, 0xFF, 0x12, 0xFE]


def test_serial_transport_errors(tmp_path: object) -> None:
    t = SerialTransport("/dev/does-not-exist-ld2451")
    with pytest.raises(TransportError, match="cannot open"):
        t.open()
    with pytest.raises(TransportError, match="not open"):
        t.read(1)
    with pytest.raises(TransportError, match="not open"):
        t.write(b"x")
    t.close()


def test_serial_transport_open_idempotent(pty_radar: PtyRadar) -> None:
    t = SerialTransport(pty_radar.path)
    t.open()
    t.open()
    assert t.is_open
    t.flush_input()
    assert t.read(10) == b""
    t.close()
    assert not t.is_open
