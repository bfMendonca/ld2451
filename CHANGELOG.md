# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `LD2451(drop_oldest=False)`: the reader thread waits for the consumer instead of
  dropping reports. Used by `ld2451 replay`.

### Fixed

- `ld2451 replay` lost reports on logs with more than ~256 frames.
- `LD2451.running` could raise `AttributeError` when racing with `stop()`.
- Commands called from a listener deadlocked the reader thread; they now fail immediately
  with `ConfigSessionError`. `stop()`/`close()` work from inside a listener.
- `monitor --stats` and `replay --stats` also print the counters on Ctrl+C.
- `config` validates values before opening the port and refuses `--factory-reset`
  combined with other changes.
- Reconnection backoff keeps growing when the port reopens and fails again.
- `restart()` is no longer retried (the module is already restarting).
- `Recorder` is thread-safe; `SerialTransport.write` no longer calls `flush()` (it could block).

## [0.1.0] - 2026-10-19

### Added

- `FrameDecoder` with resynchronisation, length and buffer limits, and per-frame timestamps.
- Encoding/decoding of every command in the V1.03 manual and of the target report.
- `LD2451` class: reader thread, queue and callbacks, config session, timeout and retry,
  verify-after-write, reconnection with backoff, counters.
- `SerialTransport`, `ReplayTransport` and `LoopbackTransport`.
- Raw byte recording/replay (`t_rx_ns,hex`).
- `ld2451 monitor | info | config | record | replay` CLI.

[Unreleased]: https://github.com/bfMendonca/ld2451/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bfMendonca/ld2451/releases/tag/v0.1.0
