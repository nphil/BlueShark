"""Framing codecs: how a command payload becomes the bytes written over BLE.

``encode`` wraps a bare payload into the exact characteristic write and
``decode`` strips that framing from a notification, returning ``None`` for
anything malformed.  Codecs hold no per-device state, so ``get_codec`` hands
out shared instances for the fixed protocols and only builds
``PrefixSuffixCodec`` per call, from user-supplied parameters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Codec(ABC):
    """One framing scheme; subclasses set ``id``/``label`` and both transforms."""

    id: str
    label: str
    # Opcodes that change persistent device state.
    destructive_opcodes: frozenset[int] = frozenset()
    # (opcode, payload) of a harmless write used to check the device responds.
    canary: tuple[int, bytes] | None = None
    # Decoded status byte -> human-readable name.
    status_names: dict[int, str] = {}
    # Destructive opcode -> plain-English reason a sweep must not probe it by default.
    # A codec with `destructive_opcodes` but no entry here for one of them is missing
    # documentation, not declaring it safe: callers fall back to a generic
    # "Unknown effect; blocked to be safe" reason (see `websocket_api.safety_notes`).
    destructive_reasons: dict[int, str] = {}

    @abstractmethod
    def encode(self, payload: bytes) -> bytes:
        """Wrap ``payload`` into the bytes written to the characteristic."""

    @abstractmethod
    def decode(self, frame: bytes) -> bytes | None:
        """Strip framing from a notification; ``None`` if ``frame`` is malformed."""

    def status(self, decoded: bytes) -> int | None:
        """Status code of a decoded notification, by default its first byte."""
        return decoded[0] if decoded else None

    def classify(self, request: bytes | None, decoded_response: bytes) -> tuple[str, int | None] | None:
        """Codec-specific ``(verdict, status)`` override consulted before the status-byte
        table; ``None`` (the default) defers to it.  See ``sweep.verdict``."""
        return None


class UnknownCodecError(KeyError):
    """Raised for a codec id that is not registered."""

    def __str__(self) -> str:
        return f"unknown codec {self.args[0]!r}"


# The submodules subclass Codec, so they can only be imported once it exists.
from .coolled import CoolLedCodec  # noqa: E402
from .iledclock import IledClockCodec  # noqa: E402
from .prefix_suffix import PrefixSuffixCodec  # noqa: E402
from .raw import RawCodec  # noqa: E402

_CODECS: tuple[type[Codec], ...] = (RawCodec, CoolLedCodec, IledClockCodec, PrefixSuffixCodec)
_SHARED: dict[str, Codec] = {
    RawCodec.id: RawCodec(),
    CoolLedCodec.id: CoolLedCodec(),
    IledClockCodec.id: IledClockCodec(),
}


def get_codec(codec_id: str, params: dict[str, str] | None = None) -> Codec:
    """Resolve ``codec_id``; ``params`` configure prefix_suffix and are ignored otherwise."""
    if codec_id == PrefixSuffixCodec.id:
        params = params or {}
        return PrefixSuffixCodec(
            header=bytes.fromhex(params.get("header_hex", "")),
            trailer=bytes.fromhex(params.get("trailer_hex", "")),
            checksum=params.get("checksum", "none"),
        )
    try:
        return _SHARED[codec_id]
    except KeyError:
        raise UnknownCodecError(codec_id) from None


def list_codecs() -> list[dict[str, str]]:
    """Codec ids and labels for UI pickers, in presentation order."""
    return [{"id": codec.id, "label": codec.label} for codec in _CODECS]
