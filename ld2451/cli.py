"""``ld2451`` command line: a thin layer over the public API."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence

from . import __version__
from .driver import LD2451
from .errors import LD2451Error
from .models import DetectionParams, Direction, RadarReport
from .protocol import BAUDRATE_INDEX
from .recorder import Recorder
from .transport import DEFAULT_BAUDRATE, ReplayTransport, SerialTransport

DIRECTIONS = {
    "away": Direction.AWAY_ONLY,
    "approach": Direction.APPROACH_ONLY,
    "both": Direction.BOTH,
}


def format_report(report: RadarReport) -> str:
    parts = [f"{report.t_rx_ns / 1e9:.6f}", f"targets={len(report.targets)}"]
    for t in report.targets:
        heading = "approaching" if t.approaching else "receding"
        parts.append(
            f"[{t.angle_deg:+d}deg {t.distance_m}m {heading} {t.speed_kmh}km/h snr={t.snr}]"
        )
    return " ".join(parts)


def report_json(report: RadarReport) -> str:
    return json.dumps(
        {
            "t_rx_ns": report.t_rx_ns,
            "alarm": report.approach_alarm,
            "targets": [dataclasses.asdict(t) for t in report.targets],
        }
    )


def _print_report(report: RadarReport, as_json: bool) -> None:
    print(report_json(report) if as_json else format_report(report), flush=True)


def _radar(args: argparse.Namespace) -> LD2451:
    return LD2451(SerialTransport(args.port, baudrate=args.baudrate))


def cmd_monitor(args: argparse.Namespace) -> int:
    with _radar(args) as radar:
        try:
            for n, report in enumerate(radar.reports(), 1):
                _print_report(report, args.json)
                if args.count and n >= args.count:
                    break
        finally:
            if args.stats:
                print(radar.stats, file=sys.stderr)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    transport = ReplayTransport(args.file, realtime=args.realtime)
    # drop_oldest=False: the reader waits for us instead of dropping reports.
    radar = LD2451(transport, reconnect=False, drop_oldest=False)
    with radar:
        try:
            while True:
                report = radar.get_report(timeout=0.2)
                if report is not None:
                    _print_report(report, args.json)
                elif transport.exhausted or not radar.running:
                    break
        finally:
            if args.stats:
                print(radar.stats, file=sys.stderr)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    radar = _radar(args)
    with Recorder(args.file) as rec:
        # Attach before the reader starts so the first bytes are not lost.
        radar.add_raw_listener(rec.write)
        with radar:
            _record_loop(args, radar, rec)
    return 0


def _record_loop(args: argparse.Namespace, radar: LD2451, rec: Recorder) -> None:
    deadline = time.monotonic() + args.duration if args.duration else None
    print(f"recording {args.port} to {args.file}; Ctrl+C to stop", file=sys.stderr)
    try:
        while deadline is None or time.monotonic() < deadline:
            report = radar.get_report(timeout=0.5)
            rec.flush()
            if report is not None and args.show:
                _print_report(report, False)
    finally:
        print(radar.stats, file=sys.stderr)


def cmd_info(args: argparse.Namespace) -> int:
    with _radar(args) as radar, radar.config_session() as session:
        fw = radar.get_firmware()
        params = radar.get_params()
        sens = radar.get_sensitivity()
    if session is not None:
        print(f"protocol version: {session.protocol_version}")
    print(f"firmware: {fw.version} (type 0x{fw.firmware_type:04X})")
    print(
        f"detection: max_distance={params.max_distance_m}m "
        f"direction={params.direction.name.lower()} "
        f"min_speed={params.min_speed_kmh}km/h no_target_delay={params.no_target_delay_s}s"
    )
    print(f"sensitivity: trigger_count={sens.trigger_count} snr_threshold={sens.snr_threshold}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    wants_params = any(
        v is not None for v in (args.max_distance, args.direction, args.min_speed, args.delay)
    )
    wants_sens = args.trigger_count is not None or args.snr_threshold is not None
    destructive = args.set_baudrate is not None or args.factory_reset
    if args.factory_reset and (wants_params or wants_sens or args.set_baudrate is not None):
        print("--factory-reset would undo the other changes; run it on its own", file=sys.stderr)
        return 2
    if destructive and not args.yes:
        print("--set-baudrate and --factory-reset need --yes", file=sys.stderr)
        return 2
    if not (wants_params or wants_sens or destructive or args.restart):
        print("nothing to configure (see ld2451 config --help)", file=sys.stderr)
        return 2
    verify = not args.no_verify
    with _radar(args) as radar, radar.config_session():
        if wants_params:
            cur = radar.get_params()
            new = DetectionParams(
                max_distance_m=_pick(args.max_distance, cur.max_distance_m),
                direction=DIRECTIONS[args.direction] if args.direction else cur.direction,
                min_speed_kmh=_pick(args.min_speed, cur.min_speed_kmh),
                no_target_delay_s=_pick(args.delay, cur.no_target_delay_s),
            )
            radar.set_params(new, verify=verify)
            print(f"detection parameters: {new}")
        if wants_sens:
            cur_s = radar.get_sensitivity()
            new_s = dataclasses.replace(
                cur_s,
                trigger_count=_pick(args.trigger_count, cur_s.trigger_count),
                snr_threshold=_pick(args.snr_threshold, cur_s.snr_threshold),
            )
            radar.set_sensitivity(new_s, verify=verify)
            print(f"sensitivity: {new_s}")
        if args.factory_reset:
            radar.factory_reset(confirm=True)
            print("factory settings restored (effective after restart)")
        if args.set_baudrate is not None:
            radar.set_baudrate(args.set_baudrate, confirm=True)
            print(f"baud rate set to {args.set_baudrate} (effective after restart)")
        if args.restart:
            radar.restart()
            print("radar restarting")
    return 0


def _ranged(low: int, high: int, also: tuple[int, ...] = ()) -> Callable[[str], int]:
    """argparse type: an int in [low, high] (or one of ``also``), checked before the port opens."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
        if not (low <= value <= high or value in also):
            extra = "".join(f" or {v}" for v in also)
            raise argparse.ArgumentTypeError(f"{value} not in {low}-{high}{extra}")
        return value

    return parse


def _pick(value: int | None, current: int) -> int:
    return current if value is None else value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ld2451", description="HLK-LD2451 radar tool")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    sub = parser.add_subparsers(dest="command", required=True)

    serial_opts = argparse.ArgumentParser(add_help=False)
    serial_opts.add_argument("--port", default="/dev/serial0", help="default: /dev/serial0")
    serial_opts.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)

    p = sub.add_parser("monitor", parents=[serial_opts], help="print live targets")
    p.add_argument("--json", action="store_true", help="one JSON object per line")
    p.add_argument("--count", type=int, default=0, help="stop after N reports")
    p.add_argument("--stats", action="store_true", help="print counters on exit")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("info", parents=[serial_opts], help="firmware and parameters")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("config", parents=[serial_opts], help="change radar settings")
    p.add_argument("--max-distance", type=_ranged(10, 255), metavar="M", help="10-255 m")
    p.add_argument("--direction", choices=sorted(DIRECTIONS))
    p.add_argument("--min-speed", type=_ranged(0, 120), metavar="KMH", help="0-120 km/h")
    p.add_argument("--delay", type=_ranged(0, 255), metavar="S", help="no-target delay, 0-255 s")
    p.add_argument(
        "--trigger-count", type=_ranged(1, 10), metavar="N", help="1-10 consecutive detections"
    )
    p.add_argument(
        "--snr-threshold", type=_ranged(3, 8, also=(0,)), metavar="N", help="0 (default 4) or 3-8"
    )
    p.add_argument("--set-baudrate", type=int, choices=sorted(BAUDRATE_INDEX))
    p.add_argument("--factory-reset", action="store_true")
    p.add_argument("--restart", action="store_true", help="restart after applying changes")
    p.add_argument("--yes", action="store_true", help="confirm destructive changes")
    p.add_argument("--no-verify", action="store_true", help="skip read-back check")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("record", parents=[serial_opts], help="record raw bytes to a log")
    p.add_argument("file")
    p.add_argument("--duration", type=float, default=0, help="seconds (default: until Ctrl+C)")
    p.add_argument("--show", action="store_true", help="also print reports")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("replay", help="decode a recorded log")
    p.add_argument("file")
    p.add_argument("--realtime", action="store_true", help="keep the original pacing")
    p.add_argument("--json", action="store_true", help="one JSON object per line")
    p.add_argument("--stats", action="store_true", help="print counters on exit")
    p.set_defaults(func=cmd_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except (LD2451Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
