from __future__ import annotations

from collections.abc import Iterator

import pytest

from ld2451 import LD2451, LoopbackTransport
from ld2451.framing import FrameDecoder, FrameKind
from ld2451.protocol import CommandCode, decode_command, encode_ack


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--hw", action="store_true", help="run tests that need a real radar")
    parser.addoption("--port", default="/dev/serial0", help="serial port for --hw tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--hw"):
        return
    skip = pytest.mark.skip(reason="needs a real radar; run with --hw")
    for item in items:
        if "hw" in item.keywords:
            item.add_marker(skip)


def h(text: str) -> bytes:
    """Hex string with arbitrary spacing -> bytes."""
    return bytes.fromhex("".join(text.split()))


class FakeRadar:
    """Answers commands like the manual says the radar does."""

    def __init__(self) -> None:
        self.params = h("64 01 05 02")
        self.sensitivity = h("01 00 00 00")
        self.firmware = h("51 24 01 01 10 15 05 24")
        self.in_config = False
        self.baud_index: int | None = None
        self.restarted = False
        self.reject: set[int] = set()
        self.silent: set[int] = set()  # never answer these
        self.drop_next: dict[int, int] = {}  # code -> number of requests to ignore
        self.received: list[int] = []
        self._decoder = FrameDecoder()

    def __call__(self, data: bytes) -> bytes | None:
        out = b""
        for frame in self._decoder.feed(data, 0):
            if frame.kind is FrameKind.COMMAND:
                code, value = decode_command(frame.payload)
                reply = self.handle(code, value)
                if reply:
                    out += reply
        return out or None

    def handle(self, code: int, value: bytes) -> bytes | None:
        self.received.append(code)
        if code in self.silent:
            return None
        if self.drop_next.get(code, 0) > 0:
            self.drop_next[code] -= 1
            return None
        if code in self.reject:
            return encode_ack(code, 1)
        if code == CommandCode.ENABLE_CONFIG:
            self.in_config = True
            return encode_ack(code, 0, h("01 00 00 00"))
        if not self.in_config:
            return None  # the manual: commands outside config mode are ignored
        if code == CommandCode.END_CONFIG:
            self.in_config = False
            return encode_ack(code)
        if code == CommandCode.SET_PARAMS:
            self.params = value
            return encode_ack(code)
        if code == CommandCode.GET_PARAMS:
            return encode_ack(code, 0, self.params)
        if code == CommandCode.SET_SENSITIVITY:
            self.sensitivity = value
            return encode_ack(code)
        if code == CommandCode.GET_SENSITIVITY:
            return encode_ack(code, 0, self.sensitivity)
        if code == CommandCode.GET_FIRMWARE:
            return encode_ack(code, 0, self.firmware)
        if code == CommandCode.SET_BAUDRATE:
            self.baud_index = int.from_bytes(value, "little")
            return encode_ack(code)
        if code == CommandCode.FACTORY_RESET:
            return encode_ack(code)
        if code == CommandCode.RESTART:
            self.restarted = True
            self.in_config = False
            return encode_ack(code)
        return encode_ack(code, 1)


@pytest.fixture
def fake() -> FakeRadar:
    return FakeRadar()


@pytest.fixture
def loopback(fake: FakeRadar) -> LoopbackTransport:
    return LoopbackTransport(responder=fake, timeout=0.01)


@pytest.fixture
def radar(loopback: LoopbackTransport) -> Iterator[LD2451]:
    r = LD2451(loopback, command_timeout=0.2, command_retries=1, reconnect_delay=0.01)
    with r:
        yield r
