"""Exception hierarchy for the LD2451 driver."""

from __future__ import annotations


class LD2451Error(Exception):
    """Base class for every error raised by this package."""


class TransportError(LD2451Error):
    """The underlying byte transport failed (port missing, unplugged, I/O error)."""


class FrameError(LD2451Error):
    """A frame or payload does not match the protocol."""


class CommandTimeout(LD2451Error):
    """No matching ACK arrived in time, after all retries."""


class CommandRejected(LD2451Error):
    """The radar answered the command with a failure status."""

    def __init__(self, code: int, status: int) -> None:
        super().__init__(f"radar rejected command 0x{code:04X} (status {status})")
        self.code = code
        self.status = status


class ConfigSessionError(LD2451Error):
    """Configuration mode could not be entered or left, or was misused."""


class VerificationError(LD2451Error):
    """Values read back after a write differ from the values written."""


class UnverifiedCommandWarning(UserWarning):
    """A command code not documented in the vendor manual was sent."""
