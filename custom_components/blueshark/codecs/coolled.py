"""CoolLED framing, as used by CoolLEDX / iLedClock BLE signs and clocks.

Frame layout: ``0x01``, big-endian 16-bit payload length, payload, ``0x03``. Every body byte
from ``0x01``..``0x03`` is byte-stuffed as ``0x02`` followed by the byte XORed with ``0x04``,
so the escape and end markers never occur unescaped inside the body. ``0x00`` is **not**
escaped: every CoolLED variant of the vendor app's frame builder (e.g.
``ILedClockUtils.java:2455`` ``getSendDataWithInfo``) only stuffs ``b > 0 && b < 4``, and a
live capture confirms it - the device-info request payload ``1f`` (length 1) frames as
``01 00 02 05 1f 03``: the length's high byte ``0x00`` rides the wire literally, only the low
byte ``0x01`` is escaped
(``/data/home/ha-iledclock/tests/live_replies_2026-09-25.json``, ``device_info.sent_frame``).
BlueShark's own encoder used to escape ``0x00`` too; devices tolerated it, but it was never
the real wire format, and is fixed here. ``decode`` stays tolerant of both encodings - an
over-escaped ``02 04`` still decodes back to ``00`` - since replies from older firmware, or
from this codec's own previous encoder, must keep parsing.

This module is the **generic, unknown-CoolLED-variant** codec: it is what a device gets when
it merely advertises CoolLED-shaped framing (name, ``fff0`` service, or ``0x3194``
manufacturer data) without identifying itself as one specific, fully mapped device. A device
that advertises the exact name ``iLedClock`` gets ``iledclock.IledClockCodec`` instead, whose
own opcode table replaces this one entirely - see that module's docstring for why one shared
table is wrong for both: iLedClock and CoolLEDX assign *different meanings to the same opcode
number* (0x08 is "brightness" on CoolLEDX and undefined on iLedClock; 0x0A is "begin
transfer" on CoolLEDX and "overwrite every timer switch" on iLedClock).

``destructive_opcodes``/``destructive_reasons`` are therefore a **conservative union**, not a
single sourced table, of every opcode known to be destructive on some CoolLED-framed device:

* iLedClock's own opcode table (decompiled ``ILedClockUtils.java`` - see ``iledclock.py``).
* CoolLEDX's public driver (github.com/UpDryTwist/coolledx-driver ``decoder.py``): ``0x0A``
  transfer, ``0x0D`` clear, ``0x23`` initialize.
* A legacy blocklist (``0x05, 0x07, 0x09, 0x0B, 0x0D, 0x0F, 0x12, 0x14``) carried over from
  before this split, whose original rationale was never recorded. Kept for variants that are
  neither of the above, purely out of caution - each entry's reason in
  ``destructive_reasons`` says so, and says what the opcode is confirmed to do on iLedClock or
  CoolLEDX specifically where that is known (the two can, and do, disagree).

Hardware-verified, over an ESPHome BLE proxy against a real iLedClock (fff1, opcode 0x08):
the device replies to a write by echoing the opcode back followed by the resulting value, not
a status byte - ``08 40`` -> ``08 FE`` (canary, no visible change) and ``08 FF`` -> ``08 FF``
(visibly changed the clock).  See ``classify`` below. That "no visible change" is now
understood, not mysterious: opcode 0x08 is not a defined iLedClock command at all (see
``iledclock.py``), so this was never evidence the canary is harmless in general - it is
harmless *because it is confirmed, on CoolLEDX, to only be a brightness write* (0x40 = 64, a
real but visible-and-harmless value on that device's 0-255 scale), not because 0x08 is
universally a no-op.
[INFERENCE]: ``status_names`` and the meaning of every other opcode.  No notification carrying
an actual status byte (as opposed to an echoed opcode+value) has ever been observed from this
hardware; the table is ported from a public driver for a shifted variant of the same protocol
family, and is only consulted when a reply does not echo the request.
"""

from __future__ import annotations

from . import Codec

_START = 0x01
_ESCAPE = 0x02
_END = 0x03
# Bytes below this are stuffed as (_ESCAPE, byte + _ESCAPE_BASE).
_ESCAPE_BASE = 0x04


class CoolLedCodec(Codec):
    id = "coolled"
    label = "CoolLED (CoolLEDX / iLedClock)"
    # Conservative union: iLedClock's own opcode table (iledclock.IledClockCodec) union
    # CoolLEDX's public-driver destructive ops (0x0A transfer, 0x0D clear, 0x23 initialize)
    # union an unsourced legacy blocklist kept for variants that are neither. See the module
    # docstring, and destructive_reasons below for what is actually known about each entry.
    destructive_opcodes = frozenset(
        {
            0x02, 0x03, 0x05, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F,
            0x12, 0x14, 0x15, 0x16, 0x1A, 0x23, 0xFE, 0xFF,
        }
    )
    destructive_reasons: dict[int, str] = {
        0x02: (
            "iLedClock: starts a program/animation upload; a bare sweep probe sends a "
            "malformed upload start and can leave the device wedged mid-upload. Meaning on "
            "other CoolLED variants is unconfirmed."
        ),
        0x03: (
            "iLedClock: one program-upload data chunk; sent out of sequence it can corrupt "
            "an in-progress upload or overwrite clock content. Meaning on other CoolLED "
            "variants is unconfirmed."
        ),
        0x05: (
            "In the legacy CoolLED deny list with no recorded rationale; kept for unknown "
            "variants out of caution. (On iLedClock itself 0x05 is the safe, reversible "
            "power switch - see IledClockCodec.)"
        ),
        0x07: (
            "In the legacy CoolLED deny list with no recorded rationale; kept for unknown "
            "variants out of caution."
        ),
        0x09: (
            "iLedClock: sets the device's date and time; a sweep probe overwrites the clock "
            "with garbage date/time fields. Also in the legacy CoolLED deny list."
        ),
        0x0A: (
            "iLedClock: overwrites every scheduled timer switch at once. CoolLEDX (public "
            "driver): begins a data transfer. Also in the legacy CoolLED deny list. Meaning "
            "on an unidentified variant is unconfirmed either way."
        ),
        0x0B: (
            "In the legacy CoolLED deny list with no recorded rationale; kept for unknown "
            "variants out of caution. (On iLedClock itself 0x0B only reads timer switches "
            "back.)"
        ),
        0x0D: (
            "CoolLEDX (public driver): clears the display. Also in the legacy CoolLED deny "
            "list. (On iLedClock itself 0x0D only checks the password, read-only.)"
        ),
        0x0E: (
            "iLedClock: sets the device password; a wrong value can lock you out of the "
            "vendor app. Meaning on other CoolLED variants is unconfirmed."
        ),
        0x0F: (
            "In the legacy CoolLED deny list with no recorded rationale; kept for unknown "
            "variants out of caution. (On iLedClock itself 0x0F is the countdown timer's "
            "status/reset/run - safe, and in its starter command map.)"
        ),
        0x12: (
            "In the legacy CoolLED deny list with no recorded rationale; kept for unknown "
            "variants out of caution."
        ),
        0x14: (
            "iLedClock: sub-op 01 overwrites all 10 night-mode bytes; a bare sweep probe "
            "(opcode + 0x01) hits that SET path with garbage instead of the harmless sub-op "
            "02 read. Also in the legacy CoolLED deny list."
        ),
        0x15: (
            "iLedClock: sub-op 01 overwrites the whole pomodoro/tomato-clock list; a bare "
            "sweep probe hits that SET path instead of the harmless sub-op 02 read. Meaning "
            "on other CoolLED variants is unconfirmed."
        ),
        0x16: (
            "iLedClock: sub-op 01 overwrites every alarm at once; a bare sweep probe hits "
            "that SET path instead of the harmless sub-op 02 read. Meaning on other CoolLED "
            "variants is unconfirmed."
        ),
        0x1A: (
            "iLedClock: sub-op 03 deletes a reminder by index; a bare sweep probe can delete "
            "reminder 0x01 instead of hitting the harmless sub-op 01 list read. Meaning on "
            "other CoolLED variants is unconfirmed."
        ),
        0x23: "CoolLEDX (public driver): initializes the device, resetting its configured state.",
        0xFE: (
            "iLedClock: starts an OTA firmware update; an interrupted or malformed transfer "
            "can brick the device. Meaning on other CoolLED variants is unconfirmed."
        ),
        0xFF: (
            "iLedClock: sends OTA firmware data; an interrupted or malformed transfer can "
            "brick the device. Meaning on other CoolLED variants is unconfirmed."
        ),
    }
    # On CoolLEDX (public driver) opcode 0x08 sets brightness 0-255; 0x40 = 64 is a real,
    # visible-but-harmless value, which is why it is safe to use as a health-check write. It
    # is not a defined iLedClock command (see iledclock.py) - the "no visible change" observed
    # when this canary was hardware-tested against a real iLedClock (module docstring above)
    # is exactly that: an undefined opcode on that specific device, not evidence the write
    # does nothing anywhere.
    canary = (0x08, bytes([0x40]))
    status_names = {
        0x00: "SUCCESS",
        0x01: "TRANSMISSION_FAILED",
        0x02: "DEVICE_ABNORMALITY",
        0x03: "DATA_ERROR",
        0x04: "DATA_LENGTH_ERROR",
        0x05: "DATA_ID_ERROR",
        0x06: "DATA_CHECKSUM_ERROR",
    }

    def encode(self, payload: bytes) -> bytes:
        frame = bytearray((_START,))
        for byte in len(payload).to_bytes(2, "big") + payload:
            if 0 < byte < _ESCAPE_BASE:
                frame.append(_ESCAPE)
                frame.append(byte ^ _ESCAPE_BASE)
            else:
                frame.append(byte)
        frame.append(_END)
        return bytes(frame)

    def decode(self, frame: bytes) -> bytes | None:
        if len(frame) < 4 or frame[0] != _START or frame[-1] != _END:
            return None
        body = bytearray()
        inner = iter(frame[1:-1])
        for byte in inner:
            if byte == _ESCAPE:
                follower = next(inner, None)
                if follower is None:
                    return None  # dangling escape
                byte = follower - _ESCAPE_BASE
                if not 0 <= byte < _ESCAPE_BASE:
                    return None  # follower outside 0x04..0x07
            body.append(byte)
        if len(body) < 2 or int.from_bytes(body[:2], "big") != len(body) - 2:
            return None
        return bytes(body[2:])

    def classify(self, request: bytes | None, decoded_response: bytes) -> tuple[str, int | None] | None:
        """``accepted`` with the echoed value when the reply echoes the request's opcode.

        Hardware-observed: this device acknowledges a write by echoing back the opcode
        byte followed by the resulting value - not a status code from ``status_names``.
        The second element of the returned tuple is that echoed *value*, reusing the wire
        `status` field for it since the wire shape is unchanged; it is not a status.
        Falls through (returns ``None``) to the status-byte table only when the reply's
        first byte does not match the opcode that was written, or when there is no request
        to compare against.
        """
        if not request or not decoded_response or decoded_response[0] != request[0]:
            return None
        value = decoded_response[1] if len(decoded_response) > 1 else None
        return "accepted", value
