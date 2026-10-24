# Contributing

Thanks for your interest in `ld2451`! Bug reports, hardware test results and pull requests
are all welcome.

## Setup

```sh
git clone https://github.com/bfMendonca/ld2451.git
cd ld2451
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Before opening a pull request

```sh
pytest                           # no hardware needed
ruff check . && ruff format --check .
mypy
```

`tox` runs the same checks on Python 3.9–3.13.

- Keep the runtime dependency list to `pyserial` only.
- Importing the package must have no side effects (no threads, ports, prints or logging setup).
- Every protocol change must be backed by the vendor manual and come with a golden test
  in `tests/test_golden.py`. Commands not in the manual are not implemented. Where the
  manual and the hardware disagree, document it in `docs/protocol.md`.
- Add an entry under *Unreleased* in `CHANGELOG.md`.

## Hardware tests

Tests marked `@pytest.mark.hw` talk to a real radar and are skipped by default:

```sh
pytest --hw --port=/dev/serial0 tests/test_hw.py
```

They restore the original parameters at the end. If you have a firmware version that isn't
listed in `docs/hardware-checks.md`, an issue with your results is very valuable.

## Reporting bugs

Please include `ld2451 --version`, `ld2451 info`, your board/OS, and if possible a raw
capture made with `ld2451 record`. It lets the problem be reproduced without your hardware.
