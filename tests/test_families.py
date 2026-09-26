"""Unit tests for the device-family fingerprint table."""

import unittest

from custom_components.blueshark.codecs import get_codec
from custom_components.blueshark.command_map import validate_command_map
from custom_components.blueshark.families import (
    FamilyConfidence,
    FingerprintInput,
    GattCharacteristic,
    GattDatabase,
    GattService,
    _tail,
    canonical_uuid,
    identify,
)


BASE = "-0000-1000-8000-00805f9b34fb"
FFF0 = "0000fff0" + BASE
FFF1 = "0000fff1" + BASE
FE95 = "0000fe95" + BASE
GOVEE_SERVICE = "00010203-0405-0607-0809-0a0b0c0d1910"
GOVEE_CHARACTERISTIC = "00010203-0405-0607-0809-0a0b0c0d1911"
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
OTHER_SERVICE = "12345678-0000-1000-8000-000000000001"
OTHER_WRITE = "12345678-0000-1000-8000-000000000002"
OTHER_NOTIFY = "12345678-0000-1000-8000-000000000003"

# A real, verified panel: 6-byte id bcdc07000001, 32x16 px, colour mode 4, firmware 0x21.
# Named for a generic, unidentified CoolLED variant: starts with "iled" (CoolLED naming) but
# is not the exact name "iLedClock", which has its own, more specific family below - see
# ILEDCLOCK_EXACT.
COOLLED_UNIDENTIFIED = FingerprintInput(
    name="iLedSign",
    service_uuids=[FFF0],
    manufacturer_data={12692: bytes.fromhex("bcdc070000011000200421")},
)
# The exact advertised name the vendor app keys its iLedClock-specific protocol off of
# (DeviceManager.java ~line 1366-1375: equalsIgnoreCase(ILED_CLOCK)).
ILEDCLOCK_EXACT = FingerprintInput(name="iLedClock")
COOLLED_GATT = GattDatabase(
    [GattService(FFF0, [GattCharacteristic(FFF1, ["WRITE", "WRITE_NO_RESPONSE", "NOTIFY"])])]
)
# frame control 0x5830 (0x08 clear), product id 0x055b, counter 10, mac 66:55:44:33:22:11.
MIBEACON = bytes([0x30, 0x58, 0x5B, 0x05, 0x0A, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])


def _coolled_with(gatt):
    return FingerprintInput(
        name=COOLLED_UNIDENTIFIED.name,
        service_uuids=COOLLED_UNIDENTIFIED.service_uuids,
        manufacturer_data=COOLLED_UNIDENTIFIED.manufacturer_data,
        gatt=gatt,
    )


class FamilyIdentificationTests(unittest.TestCase):
    def test_unidentified_coolled_advert_alone_is_a_likely_coolled_panel(self):
        top = identify(COOLLED_UNIDENTIFIED)[0]
        self.assertEqual(top.family_id, "coolled")
        self.assertEqual(top.confidence, FamilyConfidence.LIKELY)
        self.assertEqual(top.codec_id, "coolled")
        joined = " | ".join(top.evidence)
        self.assertIn("32x16", joined)
        self.assertIn("colour mode 4", joined)
        self.assertIn("firmware 0x21", joined)

    def test_matching_gatt_promotes_coolled_to_certain_with_one_more_evidence_line(self):
        without = identify(COOLLED_UNIDENTIFIED)[0]
        top = identify(_coolled_with(COOLLED_GATT))[0]
        self.assertEqual(top.family_id, "coolled")
        self.assertEqual(top.confidence, FamilyConfidence.CERTAIN)
        self.assertEqual(len(top.evidence), len(without.evidence) + 1)
        self.assertIn("CoolLED command channel confirmed", top.evidence[-1])

    def test_family_is_never_claimed_from_gatt_alone(self):
        matches = identify(FingerprintInput(gatt=COOLLED_GATT))
        self.assertNotIn("coolled", [m.family_id for m in matches])

    def test_certain_family_outranks_a_possible_command_channel(self):
        gatt = GattDatabase(
            [
                *COOLLED_GATT.services,
                GattService(
                    OTHER_SERVICE,
                    [
                        GattCharacteristic(OTHER_WRITE, ["WRITE"]),
                        GattCharacteristic(OTHER_NOTIFY, ["NOTIFY"]),
                    ],
                ),
            ]
        )
        matches = identify(_coolled_with(gatt))
        self.assertEqual(matches[0].family_id, "coolled")
        self.assertEqual(matches[0].confidence, FamilyConfidence.CERTAIN)
        self.assertTrue(
            all(m.confidence is FamilyConfidence.POSSIBLE for m in matches[1:]),
            matches,
        )

    def test_equally_confident_families_are_ordered_by_evidence_depth(self):
        matches = identify(
            FingerprintInput(
                manufacturer_data={0xEC88: b"\x01"}, service_data={FE95: MIBEACON}
            )
        )
        self.assertEqual(
            [(m.family_id, m.confidence) for m in matches],
            [
                ("xiaomi-mibeacon", FamilyConfidence.LIKELY),
                ("govee", FamilyConfidence.LIKELY),
            ],
        )

    def test_empty_advert_and_no_gatt_identifies_nothing(self):
        self.assertEqual(identify(FingerprintInput()), [])


class IledClockFamilyTests(unittest.TestCase):
    """The `iledclock` family: exact-name detection, and that it excludes `coolled`."""

    def test_exact_name_is_a_likely_iledclock_match_ranked_above_generic_coolled(self):
        matches = identify(ILEDCLOCK_EXACT)
        self.assertEqual(matches[0].family_id, "iledclock")
        self.assertEqual(matches[0].confidence, FamilyConfidence.LIKELY)
        self.assertEqual(matches[0].codec_id, "iledclock")
        self.assertNotIn("coolled", [m.family_id for m in matches])

    def test_near_miss_name_still_falls_back_to_generic_coolled(self):
        # "iLedHat" starts with "iled" (CoolLED naming) but is not the exact name the vendor
        # app keys its iLedClock-specific protocol off of.
        matches = identify(FingerprintInput(name="iLedHat", service_uuids=[FFF0]))
        self.assertNotIn("iledclock", [m.family_id for m in matches])
        self.assertEqual(matches[0].family_id, "coolled")

    def test_name_match_is_case_insensitive(self):
        matches = identify(FingerprintInput(name="ILEDCLOCK"))
        self.assertEqual(matches[0].family_id, "iledclock")

    def test_gatt_confirm_promotes_to_certain(self):
        matches = identify(FingerprintInput(name="iLedClock", gatt=COOLLED_GATT))
        self.assertEqual(matches[0].family_id, "iledclock")
        self.assertEqual(matches[0].confidence, FamilyConfidence.CERTAIN)
        self.assertIn("iLedClock command channel confirmed", matches[0].evidence[-1])

    def test_iledclock_never_claimed_from_gatt_alone(self):
        matches = identify(FingerprintInput(gatt=COOLLED_GATT))
        self.assertNotIn("iledclock", [m.family_id for m in matches])

    def test_starter_command_map_is_present_only_for_iledclock(self):
        self.assertIsNotNone(identify(ILEDCLOCK_EXACT)[0].starter_command_map)
        self.assertIsNone(identify(COOLLED_UNIDENTIFIED)[0].starter_command_map)

    def test_starter_command_map_passes_validation_unchanged_and_has_no_destructive_opcode(self):
        top = identify(ILEDCLOCK_EXACT)[0]
        starter = top.starter_command_map
        self.assertIsNotNone(starter)
        self.assertEqual(validate_command_map(starter), starter)
        codec = get_codec(top.codec_id)
        used_opcodes = set()
        for entry in starter.values():
            if entry["kind"] == "switch":
                used_opcodes.add(entry["on"]["opcode"])
                used_opcodes.add(entry["off"]["opcode"])
            else:
                used_opcodes.add(entry["opcode"])
        self.assertFalse(used_opcodes & codec.destructive_opcodes)

    def test_starter_command_map_matches_the_sourced_vendor_wire_bytes(self):
        starter = identify(ILEDCLOCK_EXACT)[0].starter_command_map
        # ILedClockUtils.java:4749-4754 + ILedClockSettingsFragment.java:115-119,186-188 +
        # the compiled settings layout's android:max="250" on bright_seek_bar (R.java:8759).
        self.assertEqual(starter["brightness"]["opcode"], 0x04)
        self.assertEqual((starter["brightness"]["min"], starter["brightness"]["max"]), (5, 255))
        # ILedClockUtils.java:5057-5062 + ILedClockRotateActivity.java:42-45.
        self.assertEqual(starter["rotation"]["opcode"], 0x0C)
        self.assertEqual((starter["rotation"]["min"], starter["rotation"]["max"]), (0, 3))
        # ILedClockUtils.java:4738-4747.
        self.assertEqual(starter["power"]["on"], {"opcode": 5, "argument_hex": "01"})
        self.assertEqual(starter["power"]["off"], {"opcode": 5, "argument_hex": "00"})
        # ILedClockUtils.java:4897-4902 getStopwatchReset() - no run byte, unlike start/stop.
        self.assertEqual(starter["stopwatch_reset"]["argument_hex"], "02")
        self.assertEqual(starter["stopwatch_start"]["argument_hex"], "0301")
        self.assertEqual(starter["stopwatch_stop"]["argument_hex"], "0300")

    def test_dedicated_integration_points_at_the_full_ha_integration(self):
        top = identify(ILEDCLOCK_EXACT)[0]
        self.assertEqual(top.dedicated_integration.domain, "iledclock")
        self.assertEqual(top.dedicated_integration.url, "https://github.com/nphil/ha-iledclock")
        self.assertIsNone(identify(COOLLED_UNIDENTIFIED)[0].dedicated_integration)


class VendorLadderTests(unittest.TestCase):
    def test_govee_manufacturer_data_is_likely_without_gatt(self):
        top = identify(FingerprintInput(manufacturer_data={0xEC88: b"\x01\x02\x03"}))[0]
        self.assertEqual(top.family_id, "govee")
        self.assertEqual(top.confidence, FamilyConfidence.LIKELY)
        self.assertIn("manufacturer 0xEC88 data 010203 is Govee's", top.evidence)

    def test_gatt_confirmation_promotes_a_name_only_claim_to_certain(self):
        possible = identify(FingerprintInput(name="GVH5075_1234"))[0]
        self.assertEqual(possible.confidence, FamilyConfidence.POSSIBLE)
        confirmed = identify(
            FingerprintInput(
                name="GVH5075_1234",
                gatt=GattDatabase(
                    [
                        GattService(
                            GOVEE_SERVICE,
                            [
                                GattCharacteristic(
                                    GOVEE_CHARACTERISTIC,
                                    ["write-without-response", "notify"],
                                )
                            ],
                        )
                    ]
                ),
            )
        )[0]
        self.assertEqual(confirmed.family_id, "govee")
        self.assertEqual(confirmed.confidence, FamilyConfidence.CERTAIN)
        self.assertIn("Govee 1910/1911 command channel confirmed", confirmed.evidence[-1])
        self.assertIn("[write-without-response, notify]", confirmed.evidence[-1])

    def test_mibeacon_service_data_reports_product_id_and_device_address(self):
        top = identify(FingerprintInput(service_data={FE95: MIBEACON}))[0]
        self.assertEqual(top.family_id, "xiaomi-mibeacon")
        self.assertEqual(top.confidence, FamilyConfidence.LIKELY)
        self.assertIn(
            "MiBeacon frame control 0x5830, product id 0x055b, counter 10, "
            "device 66:55:44:33:22:11",
            top.evidence,
        )

    def test_mibeacon_encryption_bit_is_called_out(self):
        encrypted = bytes([MIBEACON[0] | 0x08]) + MIBEACON[1:]
        top = identify(FingerprintInput(service_data={FE95: encrypted}))[0]
        self.assertEqual(top.family_id, "xiaomi-mibeacon")
        self.assertIn(
            "frame control bit 0x08 is set: the payload is encrypted, "
            "the MiBeacon AES-CCM preset applies",
            top.evidence,
        )

    def test_tuya_service_data_outranks_a_bare_service_uuid(self):
        service_only = identify(FingerprintInput(service_uuids=["0000fd50" + BASE]))[0]
        self.assertEqual(service_only.family_id, "tuya-ble")
        self.assertEqual(service_only.confidence, FamilyConfidence.POSSIBLE)
        with_data = identify(
            FingerprintInput(service_data={"0000a201" + BASE: bytes.fromhex("aabb")})
        )[0]
        self.assertEqual(with_data.family_id, "tuya-ble")
        self.assertEqual(with_data.confidence, FamilyConfidence.LIKELY)
        self.assertIn("is a Tuya BLE frame", with_data.evidence[0])

    def test_nordic_uart_is_confirmed_from_a_128_bit_service(self):
        matches = identify(
            FingerprintInput(
                service_uuids=[NUS_SERVICE],
                gatt=GattDatabase(
                    [
                        GattService(
                            NUS_SERVICE,
                            [
                                GattCharacteristic(NUS_RX, ["Write"]),
                                GattCharacteristic(NUS_TX, ["Notify"]),
                            ],
                        )
                    ]
                ),
            )
        )
        self.assertEqual(matches[0].family_id, "nordic-uart")
        self.assertEqual(matches[0].confidence, FamilyConfidence.CERTAIN)
        self.assertIsNone(matches[0].public_driver_url)
        self.assertIn("Nordic UART RX confirmed", matches[0].evidence[-1])

    def test_bedjet_is_likely_from_its_name_or_its_uuid_block(self):
        named = identify(FingerprintInput(name="BEDJET_V3"))[0]
        self.assertEqual(named.family_id, "bedjet")
        self.assertEqual(named.confidence, FamilyConfidence.LIKELY)
        blocked = identify(
            FingerprintInput(service_uuids=["00001000-bed0-0080-aa55-33cc44dd88ee"])
        )[0]
        self.assertEqual(blocked.family_id, "bedjet")
        self.assertIn("`bed0` uuid block", blocked.evidence[0])

    def test_ac_infinity_is_likely_by_manufacturer_and_possible_by_name(self):
        by_id = identify(FingerprintInput(manufacturer_data={2306: b"\xaa"}))[0]
        self.assertEqual(by_id.family_id, "ac-infinity")
        self.assertEqual(by_id.confidence, FamilyConfidence.LIKELY)
        self.assertIn("manufacturer 2306 (0x0902) data aa is AC Infinity's", by_id.evidence)
        by_name = identify(FingerprintInput(name="AC Infinity Cloudline"))[0]
        self.assertEqual(by_name.family_id, "ac-infinity")
        self.assertEqual(by_name.confidence, FamilyConfidence.POSSIBLE)

    def test_coolled_manufacturer_data_that_does_not_fit_is_reported_as_a_mismatch(self):
        for label, raw in (
            ("wrong length", bytes.fromhex("bcdc0700")),
            ("zero height", bytes.fromhex("bcdc070000010000200421")),
        ):
            with self.subTest(label):
                top = identify(FingerprintInput(manufacturer_data={12692: raw}))[0]
                self.assertEqual(top.family_id, "coolled")
                self.assertEqual(top.confidence, FamilyConfidence.POSSIBLE)
                self.assertIn(
                    f"manufacturer 0x3194 data {raw.hex()} does not fit the CoolLED "
                    "layout (11 bytes: 6-byte id, height, width16, colour, firmware)",
                    top.evidence,
                )


class CommandChannelTests(unittest.TestCase):
    def test_unrelated_write_and_notify_pair_is_reported_as_a_command_channel(self):
        matches = identify(
            FingerprintInput(
                gatt=GattDatabase(
                    [
                        GattService(
                            OTHER_SERVICE,
                            [
                                GattCharacteristic(OTHER_WRITE, ["WRITE"]),
                                GattCharacteristic(OTHER_NOTIFY, ["NOTIFY"]),
                            ],
                        )
                    ]
                )
            )
        )
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].family_id.startswith("command-channel:"), matches[0])
        self.assertEqual(matches[0].confidence, FamilyConfidence.POSSIBLE)
        self.assertEqual(len(matches[0].command_characteristic_hints), 2)
        self.assertIsNone(matches[0].codec_id)
        self.assertIn("paired with notify characteristic", matches[0].evidence[0])

    def test_single_bidirectional_characteristic_yields_one_hint(self):
        matches = identify(
            FingerprintInput(
                gatt=GattDatabase(
                    [
                        GattService(
                            OTHER_SERVICE,
                            [GattCharacteristic(OTHER_WRITE, ["WRITE", "NOTIFY"])],
                        )
                    ]
                )
            )
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(matches[0].command_characteristic_hints), 1)
        self.assertIn("both takes writes and notifies", matches[0].evidence[0])

    def test_a_service_without_both_directions_is_not_a_command_channel(self):
        for label, characteristics in (
            ("write only", [GattCharacteristic(OTHER_WRITE, ["WRITE"])]),
            ("notify only", [GattCharacteristic(OTHER_NOTIFY, ["NOTIFY"])]),
            ("no characteristics", []),
        ):
            with self.subTest(label):
                self.assertEqual(
                    identify(
                        FingerprintInput(
                            gatt=GattDatabase([GattService(OTHER_SERVICE, characteristics)])
                        )
                    ),
                    [],
                )

    def test_short_form_gatt_uuids_are_canonicalised_before_matching(self):
        matches = identify(
            FingerprintInput(
                name="CoolLEDX",
                gatt=GattDatabase(
                    [
                        GattService(
                            "FFF0",
                            [
                                GattCharacteristic("fff1", ["WRITE"]),
                                GattCharacteristic("FFF2", ["NOTIFY"]),
                            ],
                        )
                    ]
                ),
            )
        )
        self.assertEqual(matches[0].family_id, "coolled")
        self.assertEqual(matches[0].confidence, FamilyConfidence.CERTAIN)
        self.assertIn(f"GATT service {FFF0} exposes characteristic {FFF1}", matches[0].evidence[-1])
        self.assertEqual(matches[1].command_characteristic_hints, ["fff1", "fff2"])


class UuidHelperTests(unittest.TestCase):
    def test_canonical_uuid_expands_the_16_bit_short_form(self):
        self.assertEqual(canonical_uuid("FFF1"), "0000fff1-0000-1000-8000-00805f9b34fb")
        self.assertEqual(canonical_uuid("0000fff1"), "0000fff1-0000-1000-8000-00805f9b34fb")

    def test_canonical_uuid_inserts_dashes_into_an_undashed_uuid(self):
        self.assertEqual(
            canonical_uuid("000102030405060708090A0B0C0D1910"),
            "00010203-0405-0607-0809-0a0b0c0d1910",
        )

    def test_canonical_uuid_strips_braces_and_the_urn_prefix(self):
        self.assertEqual(
            canonical_uuid("  {urn:uuid:0000FFF1-0000-1000-8000-00805F9B34FB}  "),
            "0000fff1-0000-1000-8000-00805f9b34fb",
        )

    def test_canonical_uuid_rejects_anything_that_is_not_a_uuid(self):
        for raw in (None, "", "   ", "{}", "not-a-uuid", "fff", "0000fff1" + BASE + "X"):
            with self.subTest(repr(raw)):
                self.assertIsNone(canonical_uuid(raw))

    def test_tail_uses_the_16_bit_alias_or_the_last_four_characters(self):
        self.assertEqual(_tail(FFF1), "fff1")
        self.assertEqual(_tail(GOVEE_CHARACTERISTIC), "1911")
        self.assertEqual(_tail(OTHER_SERVICE), "0001")
