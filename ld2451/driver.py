"""Public driver class."""

from __future__ import annotations

import logging
import queue
import threading
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from types import TracebackType
from typing import Callable

from .clock import Clock, default_clock
from .errors import (
    CommandRejected,
    CommandTimeout,
    ConfigSessionError,
    FrameError,
    LD2451Error,
    TransportError,
    UnverifiedCommandWarning,
    VerificationError,
)
from .framing import FrameDecoder, FrameKind, RawFrame
from .models import (
    ConfigSessionInfo,
    DetectionParams,
    FirmwareVersion,
    RadarReport,
    Sensitivity,
    Stats,
)
from .protocol import (
    ENABLE_CONFIG_VALUE,
    Ack,
    CommandCode,
    decode_ack,
    decode_config_info,
    decode_firmware,
    decode_params,
    decode_report,
    decode_sensitivity,
    encode_baudrate,
    encode_command,
    encode_params,
    encode_sensitivity,
)
from .transport import TimestampedTransport, Transport

log = logging.getLogger(__name__)

ReportListener = Callable[[RadarReport], None]
RawListener = Callable[[int, bytes], None]

_KNOWN_CODES = frozenset(int(c) for c in CommandCode)


class LD2451:
    """HLK-LD2451 radar driver.

    One background thread reads the transport, splits frames and dispatches them:
    data reports go to a bounded queue and to listeners; ACKs go to an internal
    queue consumed by :meth:`send_command`.

    When the report queue is full the oldest report is dropped (live use: the
    reader never falls behind the radar). With ``drop_oldest=False`` the reader
    waits for the consumer instead (offline replay: nothing is lost).

    Nothing happens at construction: call :meth:`open` and :meth:`start`, or use
    the instance as a context manager, which does both and closes on exit.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock | None = None,
        queue_size: int = 256,
        drop_oldest: bool = True,
        read_size: int = 256,
        reconnect: bool = True,
        reconnect_delay: float = 0.2,
        reconnect_max_delay: float = 5.0,
        command_timeout: float = 1.0,
        command_retries: int = 2,
    ) -> None:
        self._transport = transport
        if clock is None:
            clock, clock_name = default_clock()
        else:
            clock_name = getattr(clock, "__name__", "custom")
        self._clock: Clock = clock
        self.clock_name = clock_name
        log.debug("receive timestamps use clock %s", clock_name)

        self._drop_oldest = drop_oldest
        self._read_size = read_size
        self._reconnect = reconnect
        self._reconnect_delay = reconnect_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._backoff = reconnect_delay
        self.command_timeout = command_timeout
        self.command_retries = command_retries

        self._decoder = FrameDecoder()
        self._reports: queue.Queue[RadarReport] = queue.Queue(maxsize=queue_size)
        self._acks: queue.Queue[Ack] = queue.Queue(maxsize=32)
        self._listeners: list[ReportListener] = []
        self._raw_listeners: list[RawListener] = []
        self._listeners_lock = threading.Lock()

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._cmd_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session_owner: int | None = None
        self._end_needed = False
        self.last_error: BaseException | None = None

        self._counters_lock = threading.Lock()
        self._invalid_payloads = 0
        self._timeouts = 0
        self._dropped_reports = 0
        self._callback_errors = 0
        self._reconnects = 0

    # ----------------------------------------------------------------- lifecycle

    def open(self) -> None:
        self._transport.open()

    def close(self) -> None:
        self.stop()
        self._transport.close()

    def __enter__(self) -> LD2451:
        self.open()
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the reader thread. Idempotent."""
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop.clear()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name="ld2451-reader", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the reader thread, waiting at most ``timeout`` seconds. Idempotent."""
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            if thread is threading.current_thread():
                # Called from a listener: the thread exits once the callback returns.
                self._thread = None
                return
            thread.join(timeout)
            if thread.is_alive():
                log.warning("reader thread did not stop within %.2fs", timeout)
            else:
                self._thread = None

    # ----------------------------------------------------------------- consumers

    def add_listener(self, callback: ReportListener) -> None:
        """Call ``callback(report)`` from the reader thread for every report.

        Keep it short: it runs on the reader thread, so it delays every later frame.
        Exceptions are logged and counted, never propagated. Commands
        (``get_params()`` etc.) cannot be sent from here; :meth:`stop` and
        :meth:`close` can.
        """
        with self._listeners_lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: ReportListener) -> None:
        with self._listeners_lock:
            self._listeners.remove(callback)

    def add_raw_listener(self, callback: RawListener) -> None:
        """Call ``callback(t_rx_ns, data)`` for every raw read (used for recording)."""
        with self._listeners_lock:
            self._raw_listeners.append(callback)

    def remove_raw_listener(self, callback: RawListener) -> None:
        with self._listeners_lock:
            self._raw_listeners.remove(callback)

    def get_report(self, timeout: float | None = None) -> RadarReport | None:
        """Next queued report, or None if none arrives within ``timeout`` (None = wait forever)."""
        try:
            return self._reports.get(timeout=timeout)
        except queue.Empty:
            return None

    def reports(self, timeout: float | None = None) -> Iterator[RadarReport]:
        """Iterate over reports.

        With a ``timeout``, ends when no report arrives for that long. Without one,
        ends once the reader thread has stopped and the queue is empty.
        """
        while True:
            report = self.get_report(timeout if timeout is not None else 0.1)
            if report is None:
                if timeout is not None or not self.running:
                    return
                continue
            yield report

    @property
    def stats(self) -> Stats:
        d = self._decoder
        with self._counters_lock:
            return Stats(
                data_frames=d.data_frames,
                command_frames=d.command_frames,
                bytes_discarded=d.bytes_discarded,
                bad_footers=d.bad_footers,
                bad_lengths=d.bad_lengths,
                invalid_payloads=self._invalid_payloads,
                timeouts=self._timeouts,
                dropped_reports=self._dropped_reports,
                callback_errors=self._callback_errors,
                reconnects=self._reconnects,
            )

    # ----------------------------------------------------------------- reader

    def _count(self, name: str) -> None:
        with self._counters_lock:
            setattr(self, name, getattr(self, name) + 1)

    def _read(self) -> tuple[bytes, int]:
        t: int | None = None
        if isinstance(self._transport, TimestampedTransport):
            data, t = self._transport.read_timestamped(self._read_size)
        else:
            data = self._transport.read(self._read_size)
        return data, (self._clock() if t is None else t)

    def _run(self) -> None:
        log.debug("reader thread started")
        try:
            while not self._stop.is_set():
                try:
                    data, t_rx_ns = self._read()
                except TransportError as exc:
                    if not self._reconnect:
                        log.error("transport failed: %s", exc)
                        self.last_error = exc
                        return
                    log.warning("transport failed: %s; reconnecting", exc)
                    if not self._reconnect_transport():
                        return
                    continue
                if data:
                    self._backoff = self._reconnect_delay
                    self._process(data, t_rx_ns)
        except Exception as exc:  # pragma: no cover - defensive, should never happen
            log.exception("reader thread crashed")
            self.last_error = exc
        finally:
            log.debug("reader thread stopped")

    def _reconnect_transport(self) -> bool:
        """Reopen the transport. The delay doubles on every failure, including a
        reopened port that fails again before delivering data."""
        while not self._stop.is_set():
            with suppress(TransportError):
                self._transport.close()
            delay = self._backoff
            self._backoff = min(delay * 2, self._reconnect_max_delay)
            if self._stop.wait(delay):
                return False
            try:
                self._transport.open()
            except TransportError as exc:
                log.warning("reconnect failed: %s; next try in %.1fs", exc, self._backoff)
                continue
            self._decoder.reset()
            self._count("_reconnects")
            log.info("transport reconnected")
            return True
        return False

    def _process(self, data: bytes, t_rx_ns: int) -> None:
        with self._listeners_lock:
            raw_listeners = list(self._raw_listeners)
        for raw_cb in raw_listeners:
            try:
                raw_cb(t_rx_ns, data)
            except Exception:
                self._count("_callback_errors")
                log.exception("raw listener failed")
        for frame in self._decoder.feed(data, t_rx_ns):
            if frame.kind is FrameKind.DATA:
                self._dispatch_report(frame)
            else:
                self._dispatch_ack(frame)

    def _dispatch_report(self, frame: RawFrame) -> None:
        try:
            report = decode_report(frame.payload, frame.t_rx_ns, frame.raw)
        except FrameError as exc:
            self._count("_invalid_payloads")
            log.debug("invalid report %s: %s", frame.raw.hex(" "), exc)
            return
        while not self._stop.is_set():
            try:
                if self._drop_oldest:
                    self._reports.put_nowait(report)
                else:
                    self._reports.put(report, timeout=0.05)
                break
            except queue.Full:
                if not self._drop_oldest:
                    continue
                try:
                    self._reports.get_nowait()
                    self._count("_dropped_reports")
                except queue.Empty:
                    pass
        with self._listeners_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(report)
            except Exception:
                self._count("_callback_errors")
                log.exception("report listener failed")

    def _dispatch_ack(self, frame: RawFrame) -> None:
        try:
            ack = decode_ack(frame.payload)
        except FrameError as exc:
            self._count("_invalid_payloads")
            log.debug("invalid ACK %s: %s", frame.raw.hex(" "), exc)
            return
        log.debug("ACK 0x%04X status %d data %s", ack.code, ack.status, ack.data.hex(" "))
        try:
            self._acks.put_nowait(ack)
        except queue.Full:
            log.warning("ACK queue full; dropping ACK for 0x%04X", ack.code)

    # ----------------------------------------------------------------- commands

    def _drain_acks(self) -> None:
        while True:
            try:
                stale = self._acks.get_nowait()
            except queue.Empty:
                return
            log.debug("discarding stale ACK for 0x%04X", stale.code)

    def _wait_ack(self, code: int, timeout: float) -> Ack | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ack = self._acks.get(timeout=remaining)
            except queue.Empty:
                return None
            if ack.code == code:
                return ack
            log.debug("discarding ACK for 0x%04X while waiting for 0x%04X", ack.code, code)

    def _transact(
        self,
        code: int,
        value: bytes = b"",
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Ack:
        timeout = self.command_timeout if timeout is None else timeout
        retries = self.command_retries if retries is None else retries
        self._check_not_reader_thread()
        frame = encode_command(code, value)
        with self._cmd_lock:
            if not self.running:
                raise ConfigSessionError("reader thread is not running; call start()")
            for attempt in range(retries + 1):
                self._drain_acks()
                log.debug("send 0x%04X (attempt %d): %s", code, attempt + 1, frame.hex(" "))
                self._transport.write(frame)
                ack = self._wait_ack(code, timeout)
                if ack is not None:
                    if not ack.ok:
                        raise CommandRejected(code, ack.status)
                    return ack
                log.warning("no ACK for 0x%04X (attempt %d/%d)", code, attempt + 1, retries + 1)
            self._count("_timeouts")
            raise CommandTimeout(f"no ACK for command 0x{code:04X} after {retries + 1} attempts")

    def _check_not_reader_thread(self) -> None:
        if threading.current_thread() is self._thread:
            raise ConfigSessionError(
                "cannot send commands from a listener callback: ACKs are delivered by the "
                "same thread; hand the work to another thread"
            )

    @property
    def in_config_session(self) -> bool:
        return self._session_owner == threading.get_ident()

    @contextmanager
    def config_session(self) -> Iterator[ConfigSessionInfo | None]:
        """Enter configuration mode for the duration of the ``with`` block.

        Sends enable-configuration (0x00FF) on entry and always sends
        end-configuration (0x00FE) on exit, except after :meth:`restart`.
        Yields the protocol version and buffer size from the ACK, if decodable.
        Starts the reader thread if needed.
        """
        self._check_not_reader_thread()
        if self.in_config_session:
            raise ConfigSessionError("already in a configuration session")
        if not self.running:
            self.start()
        with self._session_lock:
            self._session_owner = threading.get_ident()
            self._end_needed = True
            body_failed = False
            try:
                try:
                    ack = self._transact(CommandCode.ENABLE_CONFIG, ENABLE_CONFIG_VALUE)
                except LD2451Error as exc:
                    raise ConfigSessionError(f"cannot enter configuration mode: {exc}") from exc
                try:
                    info: ConfigSessionInfo | None = decode_config_info(ack.data)
                except FrameError:
                    info = None
                yield info
            except BaseException:
                body_failed = True
                raise
            finally:
                try:
                    if self._end_needed:
                        self._transact(CommandCode.END_CONFIG)
                except LD2451Error as exc:
                    log.error("cannot leave configuration mode: %s", exc)
                    if not body_failed:
                        raise ConfigSessionError(f"cannot leave configuration mode: {exc}") from exc
                finally:
                    self._session_owner = None
                    self._end_needed = False

    @contextmanager
    def _ensure_session(self) -> Iterator[None]:
        if self.in_config_session:
            yield
        else:
            with self.config_session():
                yield

    def send_command(
        self,
        code: int,
        value: bytes = b"",
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Ack:
        """Send a raw command inside :meth:`config_session` and return its ACK.

        Raises :class:`CommandRejected` on a failure status and
        :class:`CommandTimeout` when no matching ACK arrives.
        """
        if not self.in_config_session:
            raise ConfigSessionError("send_command() must run inside config_session()")
        if int(code) not in _KNOWN_CODES:
            warnings.warn(
                f"command 0x{int(code):04X} is not documented in the LD2451 manual",
                UnverifiedCommandWarning,
                stacklevel=2,
            )
        return self._transact(int(code), value, timeout, retries)

    # ----------------------------------------------------------------- high level

    def get_params(self) -> DetectionParams:
        with self._ensure_session():
            return decode_params(self._transact(CommandCode.GET_PARAMS).data)

    def set_params(self, params: DetectionParams, verify: bool = True) -> None:
        with self._ensure_session():
            self._transact(CommandCode.SET_PARAMS, encode_params(params))
            if verify:
                got = decode_params(self._transact(CommandCode.GET_PARAMS).data)
                if got != params:
                    raise VerificationError(f"wrote {params}, read back {got}")

    def get_sensitivity(self) -> Sensitivity:
        with self._ensure_session():
            return decode_sensitivity(self._transact(CommandCode.GET_SENSITIVITY).data)

    def set_sensitivity(self, sens: Sensitivity, verify: bool = True) -> None:
        with self._ensure_session():
            self._transact(CommandCode.SET_SENSITIVITY, encode_sensitivity(sens))
            if verify:
                got = decode_sensitivity(self._transact(CommandCode.GET_SENSITIVITY).data)
                if got != sens:
                    raise VerificationError(f"wrote {sens}, read back {got}")

    def get_firmware(self) -> FirmwareVersion:
        with self._ensure_session():
            return decode_firmware(self._transact(CommandCode.GET_FIRMWARE).data)

    def set_baudrate(self, baudrate: int, confirm: bool = False) -> None:
        """Persistently change the radar's baud rate. Takes effect after :meth:`restart`.

        The host port must then be reopened at the new rate.
        """
        value = encode_baudrate(baudrate)
        if not confirm:
            raise ValueError("set_baudrate() changes how to talk to the radar; pass confirm=True")
        with self._ensure_session():
            self._transact(CommandCode.SET_BAUDRATE, value)

    def factory_reset(self, confirm: bool = False) -> None:
        """Restore factory settings (including 115200 baud). Takes effect after :meth:`restart`."""
        if not confirm:
            raise ValueError("factory_reset() erases the configuration; pass confirm=True")
        with self._ensure_session():
            self._transact(CommandCode.FACTORY_RESET)

    def restart(self) -> None:
        """Restart the module. It leaves configuration mode, so no end-configuration is sent."""
        with self._ensure_session():
            # Not retried: once the ACK is sent the module is already rebooting.
            self._transact(CommandCode.RESTART, retries=0)
            self._end_needed = False
