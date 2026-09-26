"""Unit tests for the CoolLED-family codecs: framing, and the destructive-opcode deny sets."""

import unittest

from custom_components.blueshark.codecs import Codec, UnknownCodecError, get_codec, list_codecs
from custom_components.blueshark.codecs.coolled import CoolLedCodec
from custom_components.blueshark.codecs.iledclock import IledClockCodec

# iLedClock's own opcode table (see codecs/iledclock.py), which the starter command map in
# families.py must never touch.
ILEDCLOCK_DESTRUCTIVE = frozenset({0x02, 0x03, 0x09, 0x0A, 0x0E, 0x14, 0x15, 0x16, 0x1A, 0xFE, 0xFF})
# Conservative union backing the generic coolled codec (see codecs/coolled.py docstring):
# ILEDCLOCK_DESTRUCTIVE, union CoolLEDX's public-driver destructive ops (0x0A, 0x0D, 0x23),
# union the unsourced legacy blocklist (0x05, 0x07, 0x09, 0x0B, 0x0D, 0x0F, 0x12, 0x14).
COOLLED_DESTRUCTIVE = ILEDCLOCK_DESTRUCTIVE | {0x0A, 0x0D, 0x23} | {0x05, 0x07, 0x09, 0x0B, 0x0D, 0x0F, 0x12, 0x14}


class CoolLedFramingTests(unittest.TestCase):
    """The corrected escape rule: 0x00 rides the wire literally; 0x01-0x03 are stuffed."""

    def setUp(self):
        self.codec = get_codec("coolled")

    def test_encode_does_not_escape_a_zero_byte(self):
        # Live-captured device_info request
        # (/data/home/ha-iledclock/tests/live_replies_2026-09-25.json, device_info.sent_frame):
        # payload 0x1f (length 1) frames as 01 00 02 05 1f 03 - the length's high byte 0x00
        # rides the wire literally; only the low byte 0x01 is escaped.
        self.assertEqual(self.codec.encode(bytes([0x1F])).hex(), "010002051f03")

    def test_encode_escapes_bytes_one_through_three(self):
        for value in (0x01, 0x02, 0x03):
            with self.subTest(value=value):
                frame = self.codec.encode(bytes([value]))
                # Length is always 1 here, so its low byte always escapes the same way
                # (02 05); only the payload byte's escape varies with `value`.
                expected = bytes([0x01, 0x00, 0x02, 0x05, 0x02, value ^ 0x04, 0x03])
                self.assertEqual(frame, expected)

    def test_decode_still_accepts_the_older_over_escaped_zero_byte(self):
        # The previous encoder escaped 0x00 too (02 04 instead of a literal 00); decode must
        # stay tolerant of frames built that way.
        over_escaped = bytes([0x01, 0x02, 0x04, 0x02, 0x05, 0x1F, 0x03])
        self.assertEqual(self.codec.decode(over_escaped), bytes([0x1F]))

    def test_encode_decode_round_trips_every_byte_value(self):
        payload = bytes(range(256))
        self.assertEqual(self.codec.decode(self.codec.encode(payload)), payload)

    def test_decode_rejects_a_dangling_escape(self):
        # 0x02 (escape) as the very last body byte, with no follower to unstuff.
        self.assertIsNone(self.codec.decode(bytes([0x01, 0x00, 0x02, 0x03])))

    def test_decode_rejects_a_length_prefix_that_does_not_match_the_body(self):
        # Length prefix claims 5 bytes follow; only 1 actually does.
        self.assertIsNone(self.codec.decode(bytes([0x01, 0x00, 0x05, 0x1F, 0x03])))

    def test_decode_rejects_a_frame_missing_its_start_or_end_marker(self):
        self.assertIsNone(self.codec.decode(bytes([0x00, 0x00, 0x02, 0x05, 0x1F, 0x03])))
        self.assertIsNone(self.codec.decode(bytes([0x01, 0x00, 0x02, 0x05, 0x1F, 0x00])))


class DestructiveReasonsCoverageTests(unittest.TestCase):
    """Every destructive opcode a codec declares has a plain-English reason recorded."""

    def test_coolled_every_destructive_opcode_has_a_reason(self):
        codec = get_codec("coolled")
        self.assertEqual(set(codec.destructive_reasons), codec.destructive_opcodes)
        self.assertTrue(all(reason.strip() for reason in codec.destructive_reasons.values()))

    def test_iledclock_every_destructive_opcode_has_a_reason(self):
        codec = get_codec("iledclock")
        self.assertEqual(set(codec.destructive_reasons), codec.destructive_opcodes)
        self.assertTrue(all(reason.strip() for reason in codec.destructive_reasons.values()))

    def test_base_codec_defaults_to_no_declared_reasons(self):
        self.assertEqual(Codec.destructive_reasons, {})


class IledClockCodecTests(unittest.TestCase):
    def test_registered_under_its_own_id_and_shares_coolled_framing(self):
        codec = get_codec("iledclock")
        self.assertIsInstance(codec, IledClockCodec)
        self.assertIsInstance(codec, CoolLedCodec)
        self.assertEqual(codec.id, "iledclock")
        self.assertIn({"id": "iledclock", "label": codec.label}, list_codecs())
        self.assertEqual(codec.encode(bytes([0x1F])).hex(), "010002051f03")

    def test_canary_is_device_info_opcode_1f_with_no_argument(self):
        # ILedClockUtils.java:4732-4736 getDeviceInfo(): payload is the bare opcode "1f", no
        # argument byte - read-only, always replies, never changes the display.
        codec = get_codec("iledclock")
        self.assertEqual(codec.canary, (0x1F, b""))

    def test_destructive_opcodes_match_the_sourced_table(self):
        self.assertEqual(get_codec("iledclock").destructive_opcodes, ILEDCLOCK_DESTRUCTIVE)

    def test_starter_command_opcodes_are_all_non_destructive(self):
        # power (0x05), brightness (0x04), rotation (0x0C), stopwatch/countdown (0x10/0x0F),
        # scoreboard (0x11) - the opcodes families.ILEDCLOCK_STARTER_COMMAND_MAP uses.
        codec = get_codec("iledclock")
        for opcode in (0x04, 0x05, 0x0C, 0x0F, 0x10, 0x11):
            with self.subTest(opcode=hex(opcode)):
                self.assertNotIn(opcode, codec.destructive_opcodes)


class CoolLedGenericDenySetTests(unittest.TestCase):
    def test_deny_set_is_the_union_of_iledclock_coolledx_and_legacy(self):
        self.assertEqual(get_codec("coolled").destructive_opcodes, frozenset(COOLLED_DESTRUCTIVE))

    def test_zero_byte_inside_a_multi_byte_payload_is_not_escaped(self):
        codec = get_codec("coolled")
        frame = codec.encode(bytes([0x08, 0x00, 0x09]))
        # length=3 -> [0x00 literal, 0x03 escaped]; then payload 08 (literal), 00 (literal -
        # NOT stuffed as 02 04), 09 (literal).
        self.assertEqual(frame, bytes([0x01, 0x00, 0x02, 0x07, 0x08, 0x00, 0x09, 0x03]))
        self.assertEqual(codec.decode(frame), bytes([0x08, 0x00, 0x09]))


class UnknownCodecTests(unittest.TestCase):
    def test_unregistered_id_raises(self):
        with self.assertRaises(UnknownCodecError):
            get_codec("not-a-real-codec")


if __name__ == "__main__":
    unittest.main()
