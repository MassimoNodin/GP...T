class F1EngineerError(Exception):
    """Base error for expected application failures."""


class ProtocolError(F1EngineerError):
    """A datagram does not contain a valid or supported common packet header."""


class CaptureFormatError(F1EngineerError):
    """A capture file is truncated or uses an unsupported schema."""
