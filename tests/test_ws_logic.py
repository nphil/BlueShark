"""Unit tests for the pure decision functions in websocket_api.py.

Every function under test lives above `async_register_commands` in that
module and imports nothing beyond stdlib plus `.codecs`/`.command_map`/
`.families`/`.sweep` - no Home Assistant, no bleak. These tests run in a
sandbox with neither installed.
"""

import unittest

from custom_components.blueshark.codecs import get_codec
from custom_components.blueshark.command_map import CommandMapError
from custom_components.blueshark.const import (
    WS_ERROR_BUSY,
    WS_ERROR_NO_ROUTE,
    WS_ERROR_REFUSED,
    WS_ERROR_TIMEOUT,
    WS_ERROR_UNSUPPORTED,
)
from custom_components.blueshark.families import FamilyConfidence, FamilyMatch, FingerprintInput, identify
from custom_components.blueshark.sweep import SweepStep
from custom_components.blueshark.websocket_api import (
    AdvertisementSnapshot,
    DeviceNotFoundError,
    EnumeratedCharacteristic,
    EnumeratedService,
    SweepRunNotFoundError,
    confidence_score,
    decoded_facts,
    encode_for_wire,
    error_code_for_exception,
    flow_id_to_adopt,
    opcode_log_tail,
    safety_notes,
    send_result,
    shape_enumerate,
    shape_family_match,
    shape_identify,
    shape_scan_event,
    sweep_final_event,
    sweep_progress_event,
)

# Real advertisement from the iLedClock test device: manufacturer 12692 (0x3194),
# decodes to a 32x16 CoolLED panel, colour mode 4, firmware 0x21.
COOLLED_MANUFACTURER_ID = 12692
COOLLED_MFG_DATA = bytes.fromhex("bcdc070000011000200421")


class ErrorCodeForExceptionTests(unittest.TestCase):
    """Exceptions are mapped to WS_ERROR_* codes by class name (see module docstring)."""

    def test_maps_transport_exception_names(self):
        class BusyError(Exception):
            pass

        class NoRouteError(Exception):
            pass

        class UnsupportedOperationError(Exception):
            pass

        class UnknownCodecError(Exception):
            pass

        self.assertEqual(error_code_for_exception(BusyError("x")), WS_ERROR_BUSY)
        self.assertEqual(error_code_for_exception(NoRouteError("x")), WS_ERROR_NO_ROUTE)
        self.assertEqual(error_code_for_exception(UnsupportedOperationError("x")), WS_ERROR_UNSUPPORTED)
        self.assertEqual(error_code_for_exception(UnknownCodecError("x")), WS_ERROR_UNSUPPORTED)

    def test_maps_real_command_map_error_to_refused(self):
        self.assertEqual(error_code_for_exception(CommandMapError("bad map")), WS_ERROR_REFUSED)

    def test_walks_mro_for_a_more_specific_subclass(self):
        class BusyError(Exception):
            pass

        class SpecificallyBusy(BusyError):
            pass

        self.assertEqual(error_code_for_exception(SpecificallyBusy("still busy")), WS_ERROR_BUSY)

    def test_maps_local_not_found_errors(self):
        self.assertEqual(error_code_for_exception(DeviceNotFoundError("nope")), "not_found")
        self.assertEqual(error_code_for_exception(SweepRunNotFoundError("nope")), "not_found")

    def test_maps_builtin_timeout_error(self):
        self.assertEqual(error_code_for_exception(TimeoutError("slow")), WS_ERROR_TIMEOUT)

    def test_unrecognised_exception_defaults_to_refused(self):
        class TotallyUnrelatedError(Exception):
            pass

        self.assertEqual(error_code_for_exception(TotallyUnrelatedError("?")), WS_ERROR_REFUSED)


class ConfidenceScoreTests(unittest.TestCase):
    def test_grades_map_to_fixed_scores(self):
        self.assertEqual(confidence_score(FamilyConfidence.CERTAIN), 1.0)
        self.assertEqual(confidence_score(FamilyConfidence.LIKELY), 0.7)
        self.assertEqual(confidence_score(FamilyConfidence.POSSIBLE), 0.4)


class DecodedFactsTests(unittest.TestCase):
    def test_decodes_a_real_coolled_panel_payload(self):
        facts = decoded_facts({COOLLED_MANUFACTURER_ID: COOLLED_MFG_DATA}, {})
        self.assertEqual(
            facts["coolled_panel"], {"id_hex": "bcdc07000001", "width": 32, "height": 16, "colour": 4, "firmware": 33}
        )

    def test_ignores_malformed_coolled_payload(self):
        facts = decoded_facts({COOLLED_MANUFACTURER_ID: b"\x01\x02"}, {})
        self.assertNotIn("coolled_panel", facts)

    def test_decodes_mibeacon_service_data(self):
        # frame_control=0x0040 (unencrypted), product_id=0x0198, counter=1, mac FF:EE:DD:CC:BB:AA
        frame = bytes([0x40, 0x00, 0x98, 0x01, 0x01, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
        facts = decoded_facts({}, {"fe95": frame})
        self.assertEqual(facts["mibeacon"]["mac"], "FF:EE:DD:CC:BB:AA")
        self.assertFalse(facts["mibeacon"]["encrypted"])

    def test_empty_when_nothing_recognised(self):
        self.assertEqual(decoded_facts({}, {}), {})


class ShapeScanEventTests(unittest.TestCase):
    def test_recognises_the_coolled_test_device(self):
        info = AdvertisementSnapshot(
            address="01:00:00:67:0D:8A",
            name="CoolLED-Test",
            rssi=-55,
            source="proxy1",
            connectable=True,
            service_uuids=["0000fff0-0000-1000-8000-00805f9b34fb"],
            manufacturer_data={COOLLED_MANUFACTURER_ID: COOLLED_MFG_DATA},
            service_data={},
        )
        event = shape_scan_event(info)
        self.assertEqual(event["address"], "01:00:00:67:0D:8A")
        self.assertEqual(event["manufacturer_data"], {"12692": COOLLED_MFG_DATA.hex()})
        self.assertIsNotNone(event["family"])
        self.assertEqual(event["family"]["id"], "coolled")
        self.assertEqual(event["family"]["confidence"], 0.7)
        self.assertEqual(event["family"]["confidence_label"], "likely")

    def test_family_is_none_for_an_unrecognised_device(self):
        info = AdvertisementSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name="Widget",
            rssi=-70,
            source=None,
            connectable=False,
            service_uuids=[],
            manufacturer_data={},
            service_data={},
        )
        event = shape_scan_event(info)
        self.assertIsNone(event["family"])


class ShapeIdentifyTests(unittest.TestCase):
    def test_shapes_matches_and_decoded_facts_together(self):
        manufacturer_data = {COOLLED_MANUFACTURER_ID: COOLLED_MFG_DATA}
        matches = identify(
            FingerprintInput(name="CoolLED-Test", service_uuids=["fff0"], manufacturer_data=manufacturer_data)
        )
        result = shape_identify(matches, manufacturer_data, {})
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["codec_id"], "coolled")
        self.assertIn("fff1", result["matches"][0]["characteristic_hints"])
        self.assertEqual(result["decoded"]["coolled_panel"]["width"], 32)


class ShapeFamilyMatchTests(unittest.TestCase):
    def test_iledclock_match_carries_starter_map_dedicated_integration_and_safety(self):
        match = identify(FingerprintInput(name="iLedClock"))[0]
        shaped = shape_family_match(match)
        self.assertEqual(shaped["starter_command_map"]["power"]["kind"], "switch")
        self.assertEqual(shaped["dedicated_integration"]["domain"], "iledclock")
        self.assertEqual(shaped["dedicated_integration"]["url"], "https://github.com/nphil/ha-iledclock")
        self.assertEqual(
            {row["opcode"] for row in shaped["safety"]},
            {0x02, 0x03, 0x09, 0x0A, 0x0E, 0x14, 0x15, 0x16, 0x1A, 0xFE, 0xFF},
        )
        self.assertTrue(all(row["reason"] for row in shaped["safety"]))

    def test_codec_less_match_has_empty_safety_and_no_starter_map_or_integration(self):
        match = FamilyMatch(
            family_id="command-channel:1234",
            name="Command channel on 1234",
            confidence=FamilyConfidence.POSSIBLE,
            evidence=["some evidence"],
            public_driver_url=None,
            codec_id=None,
            command_characteristic_hints=["1234"],
        )
        shaped = shape_family_match(match)
        self.assertEqual(shaped["safety"], [])
        self.assertIsNone(shaped["starter_command_map"])
        self.assertIsNone(shaped["dedicated_integration"])

    def test_safety_falls_back_to_a_generic_reason_for_an_opcode_missing_its_own(self):
        # A destructive opcode with no entry in a codec's destructive_reasons must still be
        # reported as unsafe, with a fallback reason - never silently treated as safe.
        class _BareCodec:
            id = "bare"
            destructive_opcodes = frozenset({0x99})
            destructive_reasons: dict[int, str] = {}

        self.assertEqual(
            safety_notes(_BareCodec()),
            [{"opcode": 0x99, "reason": "Unknown effect; blocked to be safe"}],
        )

    def test_safety_notes_of_no_codec_is_empty(self):
        self.assertEqual(safety_notes(None), [])


class FlowIdToAdoptTests(unittest.TestCase):
    """`flow_id_to_adopt` (websocket_api.py): the pure create_entry adoption decision."""

    def test_matches_an_in_progress_flow_by_normalized_unique_id(self):
        in_progress = [{"flow_id": "abc", "context": {"unique_id": "AA:BB:CC:DD:EE:FF", "source": "bluetooth"}}]
        self.assertEqual(flow_id_to_adopt("aa:bb:cc:dd:ee:ff", in_progress), "abc")

    def test_no_match_when_no_flow_claims_that_address(self):
        in_progress = [{"flow_id": "abc", "context": {"unique_id": "11:22:33:44:55:66"}}]
        self.assertIsNone(flow_id_to_adopt("aa:bb:cc:dd:ee:ff", in_progress))

    def test_empty_in_progress_list_adopts_nothing(self):
        self.assertIsNone(flow_id_to_adopt("aa:bb:cc:dd:ee:ff", []))

    def test_flow_with_no_unique_id_in_context_is_ignored(self):
        in_progress = [{"flow_id": "abc", "context": {"source": "panel"}}]
        self.assertIsNone(flow_id_to_adopt("aa:bb:cc:dd:ee:ff", in_progress))

    def test_flow_with_no_context_at_all_is_ignored(self):
        self.assertIsNone(flow_id_to_adopt("aa:bb:cc:dd:ee:ff", [{"flow_id": "abc"}]))


class ShapeEnumerateTests(unittest.TestCase):
    def test_prefers_a_hinted_channel_over_the_first_one_found(self):
        services = [
            EnumeratedService(
                uuid="0000dead-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    EnumeratedCharacteristic(
                        uuid="0000beef-0000-1000-8000-00805f9b34fb", handle=3, properties=["write", "notify"]
                    )
                ],
            ),
            EnumeratedService(
                uuid="0000fff0-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    EnumeratedCharacteristic(
                        uuid="0000fff1-0000-1000-8000-00805f9b34fb", handle=10, properties=["write", "notify"]
                    )
                ],
            ),
        ]
        result = shape_enumerate(services, preferred_hints=["fff1"])
        self.assertEqual(
            result["suggested"],
            {
                "service": "0000fff0-0000-1000-8000-00805f9b34fb",
                "characteristic": "0000fff1-0000-1000-8000-00805f9b34fb",
                "codec_id": None,
            },
        )
        self.assertEqual(result["services"][1]["characteristics"][0]["handle"], 10)

    def test_falls_back_to_the_first_channel_without_hints(self):
        services = [
            EnumeratedService(
                uuid="0000dead-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    EnumeratedCharacteristic(
                        uuid="0000beef-0000-1000-8000-00805f9b34fb", handle=3, properties=["write", "notify"]
                    )
                ],
            )
        ]
        result = shape_enumerate(services)
        self.assertEqual(result["suggested"]["characteristic"], "0000beef-0000-1000-8000-00805f9b34fb")

    def test_suggests_nothing_without_a_write_and_notify_pair(self):
        services = [
            EnumeratedService(
                uuid="0000180a-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    EnumeratedCharacteristic(
                        uuid="00002a29-0000-1000-8000-00805f9b34fb", handle=5, properties=["read"]
                    )
                ],
            )
        ]
        result = shape_enumerate(services)
        self.assertIsNone(result["suggested"])

    def test_suggested_channel_carries_the_matched_family_codec_id(self):
        services = [
            EnumeratedService(
                uuid="0000fff0-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    EnumeratedCharacteristic(
                        uuid="0000fff1-0000-1000-8000-00805f9b34fb", handle=10, properties=["write", "notify"]
                    )
                ],
            )
        ]
        matched = shape_enumerate(services, preferred_hints=["fff1"], codec_id="coolled")
        self.assertEqual(matched["suggested"]["codec_id"], "coolled")
        unmatched = shape_enumerate(services, preferred_hints=["fff1"])
        self.assertIsNone(unmatched["suggested"]["codec_id"])


class SendResultAndWireEncodingTests(unittest.TestCase):
    def test_send_result_accepted_round_trips_a_real_coolled_frame(self):
        codec = get_codec("coolled")
        sent = codec.encode(bytes([0x08, 0xFF]))
        response = codec.encode(bytes([0x00]))
        result = send_result(sent, response, 42, codec)
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["sent_hex"], sent.hex())
        self.assertEqual(result["response_hex"], response.hex())
        self.assertEqual(result["elapsed_ms"], 42)

    def test_send_result_no_response(self):
        result = send_result(b"\x08", None, 1500, get_codec("raw"))
        self.assertEqual(result["verdict"], "no_response")
        self.assertIsNone(result["response_hex"])
        self.assertIsNone(result["status"])

    def test_send_result_recognises_a_real_echo_ack_that_changed_the_device(self):
        # Real iLedClock over an ESPHome BLE proxy, characteristic fff1: sent payload
        # 08 FF, the device visibly changed and echoed the opcode plus the same value.
        codec = get_codec("coolled")
        sent = bytes.fromhex("010204020608ff03")
        response = bytes.fromhex("0100020608ff03")
        result = send_result(sent, response, 30, codec)
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["status"], 0xFF)

    def test_send_result_recognises_a_real_echo_ack_with_no_visible_change(self):
        # Same device, canary write 08 40: echoed back as 08 FE. Under the old status-byte
        # reading this 0xFE would misclassify as rejected_other; it is an accepted echo.
        codec = get_codec("coolled")
        sent = bytes.fromhex("0102040206084003")
        response = bytes.fromhex("0100020608fe03")
        result = send_result(sent, response, 30, codec)
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["status"], 0xFE)

    def test_send_result_non_echoing_reply_still_uses_the_status_byte_table(self):
        # Reply's first byte (0x05) does not echo the request's opcode (0x08): classify()
        # declines and the generic status-byte table (0x05 == unknown id) applies.
        codec = get_codec("coolled")
        sent = codec.encode(bytes([0x08, 0xFF]))
        response = codec.encode(bytes([0x05]))
        result = send_result(sent, response, 10, codec)
        self.assertEqual(result["verdict"], "rejected_unknown_id")
        self.assertEqual(result["status"], 5)

    def test_send_result_raw_codec_is_unaffected_by_request_threading(self):
        result = send_result(b"\x08\xff", b"\x00", 5, get_codec("raw"))
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["status"], 0)

    def test_send_result_prefix_suffix_codec_is_unaffected_by_request_threading(self):
        codec = get_codec("prefix_suffix", {"header_hex": "aa", "trailer_hex": "55"})
        sent = codec.encode(bytes([0x08, 0xFF]))
        response = codec.encode(bytes([0x00]))
        result = send_result(sent, response, 5, codec)
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["status"], 0)

    def test_encode_for_wire_applies_codec_framing_unless_already_framed(self):
        codec = get_codec("coolled")
        payload = bytes([0x08, 0xFF])
        self.assertEqual(encode_for_wire(codec, payload, False), codec.encode(payload))
        self.assertEqual(encode_for_wire(codec, payload, True), payload)


class SweepEventShapeTests(unittest.TestCase):
    def test_progress_event_flags_a_canary_step_and_its_verdict(self):
        codec = get_codec("coolled")
        step = SweepStep(kind="canary", opcode=8, payload=bytes([0x08, 0x40]))
        response = codec.encode(bytes([0x00]))
        event = sweep_progress_event(1, 10, step, response, 55, codec)
        self.assertTrue(event["canary"])
        self.assertEqual(event["verdict"], "accepted")
        self.assertEqual(event["status"], 0)
        self.assertEqual(event["opcode"], 8)
        self.assertEqual(event["elapsed_ms"], 55)

    def test_progress_event_recognises_a_real_echo_ack(self):
        # Same real device/opcode vector as send_result's echo tests, threaded through
        # `step.payload` instead of a decoded `sent`.
        codec = get_codec("coolled")
        step = SweepStep(kind="probe", opcode=0x08, payload=bytes([0x08, 0xFF]))
        response = bytes.fromhex("0100020608ff03")
        event = sweep_progress_event(3, 10, step, response, 40, codec)
        self.assertEqual(event["verdict"], "accepted")
        self.assertEqual(event["status"], 0xFF)

    def test_progress_event_marks_a_probe_step_with_no_response(self):
        codec = get_codec("raw")
        step = SweepStep(kind="probe", opcode=9, payload=bytes([9]))
        event = sweep_progress_event(2, 10, step, None, 1500, codec)
        self.assertFalse(event["canary"])
        self.assertEqual(event["verdict"], "no_response")
        self.assertIsNone(event["response_hex"])

    def test_final_event_carries_the_abort_reason_when_aborted(self):
        interpretation = {
            "results": [],
            "accepted": [1, 2],
            "unknown": [3],
            "no_response": [],
            "aborted": True,
            "message": "device stopped responding after step 4",
        }
        event = sweep_final_event(interpretation)
        self.assertTrue(event["done"])
        self.assertEqual(event["aborted_reason"], "device stopped responding after step 4")
        self.assertEqual(event["accepted"], [1, 2])
        self.assertEqual(event["unknown"], [3])

    def test_final_event_has_no_abort_reason_when_it_completed(self):
        interpretation = {
            "results": [],
            "accepted": [],
            "unknown": [],
            "no_response": [],
            "aborted": False,
            "message": None,
        }
        event = sweep_final_event(interpretation)
        self.assertIsNone(event["aborted_reason"])


class OpcodeLogTailTests(unittest.TestCase):
    def test_returns_the_last_n_entries_in_order(self):
        log = [{"i": i} for i in range(5)]
        self.assertEqual(opcode_log_tail(log, 2), [{"i": 3}, {"i": 4}])

    def test_limit_zero_is_empty(self):
        self.assertEqual(opcode_log_tail([{"i": 1}], 0), [])

    def test_limit_larger_than_the_log_returns_everything(self):
        log = [{"i": 1}, {"i": 2}]
        self.assertEqual(opcode_log_tail(log, 50), log)


if __name__ == "__main__":
    unittest.main()
