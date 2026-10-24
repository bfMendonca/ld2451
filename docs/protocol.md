# HLK-LD2451 serial protocol

Source of truth: Hi-Link's *HLK-LD2451 Serial Communication Protocol V1.03* (English PDF,
not redistributed here; see [`vendor/SOURCES.md`](vendor/SOURCES.md) for download links and
checksum). Page numbers below refer to that PDF.

## Physical layer

- TTL UART, 115200 baud (default), 8 data bits, no parity, 1 stop bit (p. 3).
- All multi-byte fields are little-endian (p. 3).

## Frames

| Type | Header | Length | Body | Footer | Page |
| --- | --- | --- | --- | --- | --- |
| Command (host → radar) | `FD FC FB FA` | 2 B LE | command word (2 B) + value (N B) | `04 03 02 01` | 3 |
| ACK (radar → host) | `FD FC FB FA` | 2 B LE | command word \| `0x0100` (2 B) + return value (N B) | `04 03 02 01` | 3 |
| Data report | `F4 F3 F2 F1` | 2 B LE | see below | `F8 F7 F6 F5` | 8 |

The first field of every ACK return value is the status (2 B): `0` success, `1` failure.

## Commands

Every configuration must be preceded by "enable configuration" and followed by
"end configuration" (p. 3, 10).

| Command | Code | Value sent | ACK return (after status) | Send example | ACK example | Page |
| --- | --- | --- | --- | --- | --- | --- |
| Enable configuration | `0x00FF` | `01 00` | protocol version (2 B, `0x0001`) + buffer (2 B) | `FD FC FB FA 04 00 FF 00 01 00 04 03 02 01` | `FD FC FB FA 08 00 FF 01 00 00 01 00 00 00 04 03 02 01` | 3–4 |
| End configuration | `0x00FE` | — | — | `FD FC FB FA 02 00 FE 00 04 03 02 01` | `FD FC FB FA 04 00 FE 01 00 00 04 03 02 01` | 4 |
| Write detection parameters | `0x0002` | 4 B: max distance (m, `0x0A`–`0xFF`), direction (`00` away only, `01` approach only, `02` both), min speed (km/h, `0x00`–`0x78`), no-target delay (s, `0x00`–`0xFF`) | — | `FD FC FB FA 06 00 02 00 64 01 05 02 04 03 02 01` | `FD FC FB FA 04 00 02 01 00 00 04 03 02 01` | 4–5 |
| Read detection parameters | `0x0012` | — | 4 B, same layout as `0x0002` | `FD FC FB FA 02 00 12 00 04 03 02 01` | `FD FC FB FA 08 00 12 01 00 00 64 01 05 02 04 03 02 01` | 5 |
| Write sensitivity | `0x0003` | 4 B: consecutive trigger count (`1`–`0x0A`), SNR threshold (`0` = default 4; `3`–`8`, higher = less sensitive), reserved (`00`), reserved (`00`) | — | `FD FC FB FA 06 00 03 00 02 08 00 00 04 03 02 01` | `FD FC FB FA 04 00 03 01 00 00 04 03 02 01` | 5–6 |
| Read sensitivity | `0x0013` | — | 4 B, same layout as `0x0003` | `FD FC FB FA 02 00 13 00 04 03 02 01` | `FD FC FB FA 08 00 13 01 00 00 01 00 00 00 04 03 02 01` | 6 |
| Read firmware version | `0x00A0` | — | type (2 B, `0x2451`) + major version (2 B) + minor version (4 B) | `FD FC FB FA 02 00 A0 00 04 03 02 01` | `FD FC FB FA 0C 00 A0 01 00 00 51 24 01 01 10 15 05 24 04 03 02 01` (see divergence 3) | 6 |
| Set baud rate | `0x00A1` | 2 B: index (`1`=9600, `2`=19200, `3`=38400, `4`=57600, `5`=115200 default, `6`=230400, `7`=256000, `8`=460800) | — | `FD FC FB FA 04 00 A1 00 07 00 04 03 02 01` | `FD FC FB FA 04 00 A1 01 00 00 04 03 02 01` | 6–7 |
| Restore factory settings | `0x00A2` | — | — | `FD FC FB FA 02 00 A2 00 04 03 02 01` | `FD FC FB FA 04 00 A2 01 00 00 04 03 02 01` | 7 |
| Restart module | `0x00A3` | — | — | `FD FC FB FA 02 00 A3 00 04 03 02 01` | `FD FC FB FA 04 00 A3 01 00 00 04 03 02 01` | 8 |

Notes:

- Baud rate and factory reset are stored persistently and take effect only after the
  module restarts (p. 6–7).
- The restart happens right after the ACK (p. 8) and the radar leaves configuration mode,
  so the driver does not send `0x00FE` after it.
- The manual does not say whether detection parameters and sensitivity take effect without
  a restart. Pending hardware check (see [`hardware-checks.md`](hardware-checks.md)).
- Firmware version: the example `01 01` + `10 15 05 24` corresponds to `V1.01.24051510`,
  i.e. `V{major >> 8}.{major & 0xFF:02x}.{minor:08x}`.

## Data report (p. 8–9)

Body of the `F4 F3 F2 F1` frame:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | number of targets |
| 1 | 1 | alarm: `01` a target is approaching, `00` none |
| 2 + 5k | 1 | angle: degrees = value − `0x80` |
| 3 + 5k | 1 | distance (m, 0–100) |
| 4 + 5k | 1 | direction: `00` approaching, `01` receding |
| 5 + 5k | 1 | speed (km/h, 0–120) |
| 6 + 5k | 1 | SNR (0–255) |

Length 0 (`F4 F3 F2 F1 00 00 F8 F7 F6 F5`) means no target. The driver accepts up to
5 targets (maximum length 27) and computes the number of targets as `(length − 2) / 5`,
without relying on byte 0.

Example from the manual (p. 9), three targets:

```
F4 F3 F2 F1 11 00 03 01 8A 28 00 3C 15 8A 1E 01 3C 0F 76 5F 00 3C 0F F8 F7 F6 F5
```

| Target | Angle | Distance | Direction | Speed | SNR |
| --- | --- | --- | --- | --- | --- |
| 1 | +10° | 40 m | approaching | 60 km/h | 21 |
| 2 | +10° | 30 m | receding | 60 km/h | 15 |
| 3 | −10° | 95 m | approaching | 60 km/h | 15 |

## Divergences between the original design and the manual

The manual wins in every case.

1. **Target direction.** The design mentioned "direction 01/00" without an order. The
   manual defines `00` = approaching and `01` = receding (table and example, p. 9).
   `Target.approaching` is `True` for `00`.
2. **Order of the first 2 report bytes.** Table 9 lists "number of targets, alarm", but the
   walkthrough of the example mentions the alarm first. The example bytes (`03 01` with
   three targets) confirm the table: byte 0 = count, byte 1 = alarm.
3. **Firmware ACK length.** The example (p. 6) shows length `0B 00` (11), but the body
   bytes add up to 12 (`A0 01 00 00 51 24 01 01 10 15 05 24`). Treated as a typo: the
   golden test uses `0C 00`.
   **Confirmed on hardware (2026-10-20):** the radar (firmware V1.13.25070114) replies
   with `0C 00` and the ACK decodes without error.
4. **Bluetooth and MAC.** The design planned `set_bluetooth` and a MAC read (the
   `0x00A0`–`0x00A5` range used by other LD24xx modules). LD2451 V1.03 does not document
   these commands. Following the rule "commands not in the manual are not implemented",
   they were removed from the API and the CLI (`--bluetooth`).
5. **OCR in the report example.** The PDF shows `IE` in the second target; from context
   (30 m) it is `1E`.
6. **Engineering mode.** The manual mentions an engineering mode with per-gate energies
   (p. 8) but documents neither how to enable it nor its format. Not implemented.
7. **No `UNVERIFIED` items.** Every command from the design that remains in the driver is
   in the manual with a code and an example. `UnverifiedCommandWarning` still exists and is
   emitted when `send_command` is called with a code outside `CommandCode`.

## Observed on hardware (2026-10-20)

Radar with firmware `V1.13.25070114` (type `0x2451`), newer than the V1.01 in the manual,
connected to a Pi Zero on `/dev/serial0`.

- Every command used by the driver (`0x00FF`, `0x00FE`, `0x0002`, `0x0012`, `0x0003`,
  `0x0013`, `0x00A0`) replies on the first attempt, within 10–60 ms.
- Enable-configuration ACK: protocol version `0x0001`, buffer `0x0000`.
- With no target, the radar sends an empty frame (`F4 F3 F2 F1 00 00 F8 F7 F6 F5`) about
  every 1.39 s.
- Factory settings of this unit: max distance 100 m, approach only, min speed 1 km/h,
  delay 1 s; trigger count 2, SNR threshold 0 (default).
- Real capture in `tests/fixtures/real_idle.csv`.

## Known decoder limitation

A spurious or truncated header with a plausible length (up to 27 for data, 64 for
commands) makes the decoder wait for that many bytes before discarding the frame and
resynchronising. With no target (10 bytes every ~1.39 s) this can delay delivery by 3 to
10 s. No valid frame is lost and timestamps remain correct.
