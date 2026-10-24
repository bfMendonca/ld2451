# Checks that only real hardware can confirm

Run on the Pi Zero with the radar connected (see wiring in the README). Install first:
`pip install -e '.[dev]'`.

Status as of 2026-10-20 (firmware V1.13.25070114): ✅ confirmed, ⏳ pending.

| # | Status | What to confirm | Command |
| --- | --- | --- | --- |
| 1 | ✅ | Data frames arrive and decode without invalid footers | `pytest --hw --port=/dev/serial0 tests/test_hw.py::test_receives_reports` |
| 2 | ✅ | Firmware ACK: actual length (`0C 00`, or `0B 00` as in the manual) and type `0x2451` | `ld2451 -vv info 2>&1 \| grep "ACK 0x00A0"`; then `pytest --hw tests/test_hw.py::test_firmware -s` |
| 3 | ✅ | Detection parameters round-trip | `pytest --hw tests/test_hw.py::test_params_roundtrip` |
| 4 | ✅ | Sensitivity round-trip | `pytest --hw tests/test_hw.py::test_sensitivity_roundtrip` |
| 5 | ✅ | The radar resumes reporting after leaving configuration mode | `pytest --hw tests/test_hw.py::test_reports_resume_after_session` |
| 6 | ⏳ | Parameters take effect without a restart (the manual doesn't say) | `ld2451 config --max-distance 20` and, with a target at ~30 m, `ld2451 monitor --count 50`: no target beyond 20 m should appear |
| 7 | ⏳ | Direction `00` = approaching (manual p. 9) | `ld2451 monitor` while walking towards the radar: `approaching` |
| 8 | ⏳ | Order of report bytes 0/1 (count, alarm) | `ld2451 monitor --json --count 20`: `alarm` should be `true` only with an approaching target |
| 9 | ⏳ | Changing the baud rate and back to default | `ld2451 config --set-baudrate 230400 --yes --restart`; `ld2451 info --baudrate 230400`; `ld2451 config --baudrate 230400 --set-baudrate 115200 --yes --restart` |
| 10 | ⏳ | Reconnection: power-cycle the radar during `ld2451 monitor --stats` | `ld2451 -v monitor --stats`, disconnect 5 V for a few seconds; it should log "reconnected" (if `/dev/serial0` doesn't disappear, only the counters change) |
| 11 | ✅ | Recording and replaying a real session | `ld2451 record tests/fixtures/real_session.csv --duration 30` and `ld2451 replay tests/fixtures/real_session.csv` |

Notes from the 2026-10-20 run:

- 1–5: `pytest --hw` 5/5 passed; the original parameters were restored.
- 2: the radar sends length `0C 00` (the manual says `0B 00`).
- 11: an idle capture is saved in `tests/fixtures/real_idle.csv`.
- 6, 7 and 8 need a moving target in front of the radar; 9 writes the baud rate
  persistently; 10 requires physically powering off the radar.

Contributions of results from other firmware versions are welcome. Please open an issue
with the output of `ld2451 info`.
