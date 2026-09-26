"""Device-family fingerprints for BLE adverts and GATT tables.

Pure stdlib: this module sits on the import chain of the package and the unit
tests, so it must never pull in Home Assistant or a BLE stack.

A family is only ever claimed from the *advertisement* (name, service uuids,
manufacturer data, service data).  A GATT table can confirm a claimed family,
promoting it to CERTAIN, or, independently of any family, describe the generic
write/notify command channels the device exposes.  Every match carries the
human-readable evidence behind it so the panel can show its reasoning.
"""

from __future__ import annotations

import re
import uuid as uuid_mod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .command_map import validate_command_map


class FamilyConfidence(str, Enum):
    CERTAIN = "certain"
    LIKELY = "likely"
    POSSIBLE = "possible"


_CONFIDENCE_RANK = {
    FamilyConfidence.CERTAIN: 0,
    FamilyConfidence.LIKELY: 1,
    FamilyConfidence.POSSIBLE: 2,
}


@dataclass(frozen=True)
class DedicatedIntegration:
    """A full, separately-installable HA integration that already covers this family."""

    domain: str
    name: str
    url: str
    summary: str


@dataclass(frozen=True)
class FamilyMatch:
    family_id: str
    name: str
    confidence: FamilyConfidence
    evidence: list[str]
    public_driver_url: str | None
    codec_id: str | None
    command_characteristic_hints: list[str]
    starter_command_map: dict[str, dict[str, Any]] | None = None
    dedicated_integration: DedicatedIntegration | None = None


@dataclass(frozen=True)
class GattCharacteristic:
    uuid: str
    properties: list[str]


@dataclass(frozen=True)
class GattService:
    uuid: str
    characteristics: list[GattCharacteristic]


@dataclass(frozen=True)
class GattDatabase:
    services: list[GattService]


@dataclass(frozen=True)
class FingerprintInput:
    name: str | None = None
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_data: dict[str, bytes] = field(default_factory=dict)
    gatt: GattDatabase | None = None


# --- UUID canonicalisation ---------------------------------------------------

_HEX_DIGITS_RE = re.compile(r"[0-9a-fA-F]+")
_DASHED_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_URN_PREFIX = "urn:uuid:"


def canonical_uuid(raw: str | None) -> str | None:
    """Normalise any accepted uuid spelling to the dashed lowercase 128-bit form.

    Accepts 16-bit (``fff1``) and 32-bit (``0000fff1``) Bluetooth short forms,
    undashed and dashed 128-bit forms, optionally wrapped in braces or prefixed
    with ``urn:uuid:``.  Anything else is ``None``.
    """
    if raw is None:
        return None
    text = raw.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if text.startswith(_URN_PREFIX):
        text = text[len(_URN_PREFIX) :]
    if not text:
        return None
    if _HEX_DIGITS_RE.fullmatch(text):
        if len(text) == 4:
            dashed = f"0000{text.lower()}-0000-1000-8000-00805f9b34fb"
        elif len(text) == 8:
            dashed = f"{text.lower()}-0000-1000-8000-00805f9b34fb"
        elif len(text) == 32:
            dashed = (
                f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"
            ).lower()
        else:
            return None
    elif _DASHED_UUID_RE.fullmatch(text):
        dashed = text.lower()
    else:
        return None
    try:
        return str(uuid_mod.UUID(dashed))
    except ValueError:
        return None


# --- GATT property helpers ---------------------------------------------------

GATT_WRITE_PROPERTIES = {"WRITE", "WRITE_NO_RESPONSE", "WRITE_WITHOUT_RESPONSE"}
GATT_NOTIFY_PROPERTIES = {"NOTIFY", "INDICATE"}


def _normalise_prop(p: str) -> str:
    return p.strip().upper().replace("-", "_")


def _writable(properties: list[str]) -> bool:
    return any(_normalise_prop(p) in GATT_WRITE_PROPERTIES for p in properties)


def _notifiable(properties: list[str]) -> bool:
    return any(_normalise_prop(p) in GATT_NOTIFY_PROPERTIES for p in properties)


def _property_list(props: list[str]) -> str:
    """Render properties for evidence text, keeping the caller's casing."""
    return "[" + ", ".join(props) + "]"


# --- Advertisement view ------------------------------------------------------


class Advert:
    """Case-folded, uuid-canonicalised view of one advertisement."""

    def __init__(self, input: FingerprintInput) -> None:
        self.name = (input.name or "").strip()
        self._lower_name = self.name.lower()
        self.services = [
            c for s in input.service_uuids if (c := canonical_uuid(s)) is not None
        ]
        self._service_data = [
            (c, v)
            for k, v in input.service_data.items()
            if (c := canonical_uuid(k)) is not None
        ]
        self._manufacturer_data = input.manufacturer_data

    def name_starts_with(self, *prefixes: str) -> bool:
        return any(self._lower_name.startswith(p) for p in prefixes)

    def name_equals(self, name: str) -> bool:
        return self._lower_name == name.lower()

    def name_contains(self, fragment: str) -> bool:
        return fragment in self._lower_name

    def service(self, fragment: str) -> str | None:
        return next((s for s in self.services if fragment in s), None)

    def service_data(self, fragment: str) -> tuple[str, bytes] | None:
        return next((sd for sd in self._service_data if fragment in sd[0]), None)

    def manufacturer(self, id: int) -> bytes | None:
        return self._manufacturer_data.get(id)


# --- GATT view ---------------------------------------------------------------


@dataclass(frozen=True)
class CommandChannel:
    service: str
    write: str
    write_properties: list[str]
    notify: str
    notify_properties: list[str]

    @property
    def bidirectional(self) -> bool:
        return self.write == self.notify


@dataclass(frozen=True)
class _ViewCharacteristic:
    uuid: str
    properties: list[str]
    can_write: bool
    can_notify: bool


@dataclass(frozen=True)
class _ViewService:
    uuid: str
    characteristics: list[_ViewCharacteristic]


def _gatt_uuid(raw: str) -> str:
    """Canonicalise a GATT uuid, falling back to a lowercased copy of odd input."""
    return canonical_uuid(raw) or raw.strip().lower()


class GattView:
    """Canonicalised view of a GATT table; ``None`` behaves as an empty table."""

    def __init__(self, database: GattDatabase | None) -> None:
        services = database.services if database is not None else []
        self._services = [
            _ViewService(
                uuid=_gatt_uuid(service.uuid),
                characteristics=[
                    _ViewCharacteristic(
                        uuid=_gatt_uuid(characteristic.uuid),
                        properties=characteristic.properties,
                        can_write=_writable(characteristic.properties),
                        can_notify=_notifiable(characteristic.properties),
                    )
                    for characteristic in service.characteristics
                ],
            )
            for service in services
        ]

    @property
    def is_empty(self) -> bool:
        return not self._services

    def pair(
        self, service_fragment: str, characteristic_fragment: str
    ) -> tuple[str, str, list[str]] | None:
        """Find the first service/characteristic pair matching both fragments."""
        for service in self._services:
            if service_fragment not in service.uuid:
                continue
            for characteristic in service.characteristics:
                if characteristic_fragment in characteristic.uuid:
                    return service.uuid, characteristic.uuid, characteristic.properties
        return None

    def command_channels(self) -> list[CommandChannel]:
        """One channel per service that both takes writes and can answer."""
        channels: list[CommandChannel] = []
        for service in self._services:
            writes = [c for c in service.characteristics if c.can_write]
            notifies = [c for c in service.characteristics if c.can_notify]
            if not writes or not notifies:
                continue
            dual = next((c for c in writes if c.can_notify), None)
            write = dual or writes[0]
            notify = dual or notifies[0]
            channels.append(
                CommandChannel(
                    service=service.uuid,
                    write=write.uuid,
                    write_properties=write.properties,
                    notify=notify.uuid,
                    notify_properties=notify.properties,
                )
            )
        return channels


# --- Payload decoders --------------------------------------------------------


@dataclass(frozen=True)
class CoolLedPanel:
    id_hex: str
    width: int
    height: int
    colour: int
    firmware: int


def decode_coolled_panel(data: bytes) -> CoolLedPanel | None:
    """Decode CoolLED manufacturer data: 6-byte id, height, width16, colour, firmware."""
    if len(data) != 11:
        return None
    height = data[6]
    width = (data[7] << 8) | data[8]
    if height == 0 or width == 0:
        return None
    return CoolLedPanel(
        id_hex=data[0:6].hex(),
        width=width,
        height=height,
        colour=data[9],
        firmware=data[10],
    )


@dataclass(frozen=True)
class MiBeaconFrame:
    frame_control: int
    product_id: int
    counter: int
    mac: str
    encrypted: bool


def decode_mibeacon(data: bytes) -> MiBeaconFrame | None:
    """Decode the fixed MiBeacon header: frame control, product id, counter, mac."""
    if len(data) < 11:
        return None
    frame_control = data[0] | (data[1] << 8)
    product_id = data[2] | (data[3] << 8)
    mac = ":".join(f"{data[i]:02X}" for i in (10, 9, 8, 7, 6, 5))
    return MiBeaconFrame(
        frame_control=frame_control,
        product_id=product_id,
        counter=data[4],
        mac=mac,
        encrypted=bool(frame_control & 0x08),
    )


# --- Family table ------------------------------------------------------------

COOLLED_MANUFACTURER = 0x3194
GOVEE_MANUFACTURER = 0xEC88
TELINK_MANUFACTURER = 0x0211
AC_INFINITY_MANUFACTURER = 2306


@dataclass(frozen=True)
class _Detection:
    confidence: FamilyConfidence
    evidence: list[str]


def _never_confirms(gatt: GattView) -> str | None:
    return None


@dataclass
class _Family:
    id: str
    name: str
    detect: Callable[[Advert], _Detection | None]
    driver_url: str | None = None
    codec_id: str | None = None
    hints: list[str] = field(default_factory=list)
    confirm: Callable[[GattView], str | None] = _never_confirms
    starter_command_map: dict[str, dict[str, Any]] | None = None
    dedicated_integration: DedicatedIntegration | None = None


def _confirm_pair(
    gatt: GattView, service_fragment: str, characteristic_fragment: str, verdict: str
) -> str | None:
    pair = gatt.pair(service_fragment, characteristic_fragment)
    if pair is None:
        return None
    service, characteristic, properties = pair
    return (
        f"GATT service {service} exposes characteristic {characteristic} "
        f"{_property_list(properties)}: {verdict}"
    )


def _detect_iledclock(advert: Advert) -> _Detection | None:
    if not advert.name_equals("iledclock"):
        return None
    return _Detection(
        FamilyConfidence.LIKELY,
        [f'advertised name "{advert.name}" is exactly "iLedClock"'],
    )


def _confirm_iledclock(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "0000fff0-", "0000fff1-", "iLedClock command channel confirmed"
    )


# Every command below is verified against the decompiled vendor app
# (/data/home/tmp/led1248/src/sources/com/jtkj/led1248/light/), one starter entry per
# ILedClockUtils.java builder function, restricted to safe, reversible actions the existing
# command_map schema can already express.
_ILEDCLOCK_STARTER_COMMAND_MAP_RAW: dict[str, dict[str, Any]] = {
    # ILedClockUtils.java:4738-4747 getSwitchData(bool): payload ["05", "01"|"00"].
    "power": {
        "name": "Power",
        "kind": "switch",
        "on": {"opcode": 0x05, "argument_hex": "01"},
        "off": {"opcode": 0x05, "argument_hex": "00"},
    },
    # ILedClockUtils.java:4749-4754 getSetBrightness(int): payload ["04", value]. Range
    # verified in ILedClockSettingsFragment.java: progress = brightness - 5, floored at 0
    # (lines 115-119); value sent = progress + 5 (lines 186-188); the compiled layout
    # (res/layout/i_led_clock_settings_fragment.xml inside the APK, resource id
    # bright_seek_bar = R.java:8759 0x7f0900a4) sets android:max="250" - so the UI only ever
    # sends 5..255, never 0..100 or 0..255.
    "brightness": {
        "name": "Brightness",
        "kind": "number",
        "opcode": 0x04,
        "min": 5,
        "max": 255,
    },
    # ILedClockUtils.java:5057-5062 setRotate(int): payload ["0c", value]. Only ever called
    # with 0-3: ILedClockRotateActivity.java:42-45 builds exactly 4 fixed list rows (values
    # 0,1,2,3), :50 posts RotateILedClockSetEvent(i) with that row's own value, and
    # DeviceManager.java:6967-6968 forwards `action` straight into setRotate unmodified.
    "rotation": {
        "name": "Rotation",
        "kind": "number",
        "opcode": 0x0C,
        "min": 0,
        "max": 3,
    },
    # ILedClockUtils.java:4904-4914 getStopwatchStartOrStop(bool): payload ["10","03","01"|"00"].
    "stopwatch_start": {
        "name": "Stopwatch: start",
        "kind": "button",
        "opcode": 0x10,
        "argument_hex": "0301",
    },
    "stopwatch_stop": {
        "name": "Stopwatch: stop",
        "kind": "button",
        "opcode": 0x10,
        "argument_hex": "0300",
    },
    # ILedClockUtils.java:4897-4902 getStopwatchReset(): payload ["10","02"], no further bytes.
    "stopwatch_reset": {
        "name": "Stopwatch: reset",
        "kind": "button",
        "opcode": 0x10,
        "argument_hex": "02",
    },
    # ILedClockUtils.java:4933-4943 getCountDownStartOrStop(bool): payload ["0f","03","01"|"00"].
    # (Countdown reset needs h/m/s - getCountDownReset(int,int,int), ILedClockUtils.java:
    # 4923-4931 - so it takes user input and cannot be a static starter button; omitted.)
    "countdown_start": {
        "name": "Countdown: start",
        "kind": "button",
        "opcode": 0x0F,
        "argument_hex": "0301",
    },
    "countdown_stop": {
        "name": "Countdown: stop",
        "kind": "button",
        "opcode": 0x0F,
        "argument_hex": "0300",
    },
    # ILedClockUtils.java:4977-4987 getScoreBoardStartOrStop(bool): payload ["11","04","01"|"00"].
    "scoreboard_start": {
        "name": "Scoreboard: start",
        "kind": "button",
        "opcode": 0x11,
        "argument_hex": "0401",
    },
    "scoreboard_stop": {
        "name": "Scoreboard: stop",
        "kind": "button",
        "opcode": 0x11,
        "argument_hex": "0400",
    },
}
# Validated once at import time: this constant IS validate_command_map's normalized output,
# so "passes validate_command_map unchanged" is true by construction (also asserted in
# tests/test_families.py for anyone who edits the raw dict above).
ILEDCLOCK_STARTER_COMMAND_MAP: dict[str, dict[str, Any]] = validate_command_map(
    _ILEDCLOCK_STARTER_COMMAND_MAP_RAW
)

ILEDCLOCK_DEDICATED_INTEGRATION = DedicatedIntegration(
    domain="iledclock",
    name="iLedClock",
    url="https://github.com/nphil/ha-iledclock",
    summary="Clock faces, pixel art and animations, alarms, timers and night mode",
)


def _detect_coolled(advert: Advert) -> _Detection | None:
    if advert.name_equals("iledclock"):
        # iLedClock advertises CoolLED framing but has its own opcode table and its own,
        # more specific family below (codec_id="iledclock"); do not double-claim it here.
        return None
    evidence: list[str] = []
    named = advert.name_starts_with("coolled", "iled")
    if named:
        evidence.append(
            f'advertised name "{advert.name}" matches CoolLED naming (CoolLED*/iLed*)'
        )
    service = advert.service("0000fff0-")
    if service is not None:
        evidence.append(
            f"advertised service {service} (fff0) is the CoolLED command service"
        )
    raw = advert.manufacturer(COOLLED_MANUFACTURER)
    panel = None
    if raw is not None:
        panel = decode_coolled_panel(raw)
        if panel is not None:
            evidence.append(
                f"manufacturer 0x3194 data {raw.hex()}: 6-byte id {panel.id_hex}, "
                f"panel {panel.width}x{panel.height} px, colour mode {panel.colour}, "
                f"firmware 0x{panel.firmware:02x}"
            )
        else:
            evidence.append(
                f"manufacturer 0x3194 data {raw.hex()} does not fit the CoolLED layout "
                "(11 bytes: 6-byte id, height, width16, colour, firmware)"
            )
    if panel is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if named and service is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if evidence:
        return _Detection(FamilyConfidence.POSSIBLE, evidence)
    return None


def _confirm_coolled(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "0000fff0-", "0000fff1-", "CoolLED command channel confirmed"
    )


def _detect_xiaomi_mibeacon(advert: Advert) -> _Detection | None:
    data = advert.service_data("0000fe95-")
    service = advert.service("0000fe95-")
    if data is not None:
        evidence = [f"service data {data[0]} (fe95) {data[1].hex()} is a MiBeacon frame"]
        frame = decode_mibeacon(data[1])
        if frame is not None:
            evidence.append(
                f"MiBeacon frame control 0x{frame.frame_control:04x}, "
                f"product id 0x{frame.product_id:04x}, counter {frame.counter}, "
                f"device {frame.mac}"
            )
            if frame.encrypted:
                evidence.append(
                    "frame control bit 0x08 is set: the payload is encrypted, "
                    "the MiBeacon AES-CCM preset applies"
                )
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if service is not None:
        return _Detection(
            FamilyConfidence.POSSIBLE,
            [
                f"advertised service {service} (fe95) is Xiaomi's, "
                "but no fe95 service data was seen"
            ],
        )
    return None


def _detect_govee(advert: Advert) -> _Detection | None:
    evidence: list[str] = []
    raw = advert.manufacturer(GOVEE_MANUFACTURER)
    if raw is not None:
        evidence.append(f"manufacturer 0xEC88 data {raw.hex()} is Govee's")
    named = advert.name_starts_with("govee", "gvh", "ihoment")
    if named:
        evidence.append(
            f'advertised name "{advert.name}" matches Govee naming (Govee_*/GVH*/ihoment*)'
        )
    if raw is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if named:
        return _Detection(FamilyConfidence.POSSIBLE, evidence)
    return None


def _confirm_govee(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "0a0b0c0d1910", "0a0b0c0d1911", "Govee 1910/1911 command channel confirmed"
    )


def _detect_telink_mesh(advert: Advert) -> _Detection | None:
    evidence: list[str] = []
    service = advert.service("1910") or advert.service("1911")
    if service is not None:
        evidence.append(
            f"advertised service {service} is Telink's mesh service (1910/1911)"
        )
    raw = advert.manufacturer(TELINK_MANUFACTURER)
    if raw is not None:
        evidence.append(f"manufacturer 0x0211 data {raw.hex()} is Telink's")
    if service is not None or raw is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    return None


def _confirm_telink_mesh(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "0a0b0c0d1910", "0a0b0c0d1911", "Telink 1910/1911 command channel confirmed"
    )


def _detect_tuya_ble(advert: Advert) -> _Detection | None:
    evidence: list[str] = []
    data = advert.service_data("0000fd50-") or advert.service_data("0000a201-")
    if data is not None:
        evidence.append(f"service data {data[0]} {data[1].hex()} is a Tuya BLE frame")
    service = advert.service("0000fd50-")
    if service is not None:
        evidence.append(f"advertised service {service} (fd50) is Tuya's")
    if data is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if service is not None:
        return _Detection(FamilyConfidence.POSSIBLE, evidence)
    return None


def _confirm_tuya_ble(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "0000a201-", "0000a202-", "Tuya a201/a202 command channel confirmed"
    )


def _detect_nordic_uart(advert: Advert) -> _Detection | None:
    service = advert.service("6e400001-b5a3")
    if service is None:
        return None
    return _Detection(
        FamilyConfidence.LIKELY,
        [
            f"advertised service {service} is the Nordic UART service "
            "(RX 6e400002, TX 6e400003)"
        ],
    )


def _confirm_nordic_uart(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "6e400001-b5a3", "6e400002-b5a3", "Nordic UART RX confirmed"
    )


def _detect_bedjet(advert: Advert) -> _Detection | None:
    evidence: list[str] = []
    service = advert.service("-bed0-")
    if service is not None:
        evidence.append(f"advertised service {service} carries BedJet's `bed0` uuid block")
    named = advert.name_starts_with("bedjet")
    if named:
        evidence.append(f'advertised name "{advert.name}" matches BedJet naming')
    if service is not None or named:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    return None


def _confirm_bedjet(gatt: GattView) -> str | None:
    return _confirm_pair(
        gatt, "00001000-bed0", "00002004-bed0", "BedJet command characteristic confirmed"
    )


def _detect_ac_infinity(advert: Advert) -> _Detection | None:
    evidence: list[str] = []
    raw = advert.manufacturer(AC_INFINITY_MANUFACTURER)
    if raw is not None:
        evidence.append(f"manufacturer 2306 (0x0902) data {raw.hex()} is AC Infinity's")
    named = advert.name_contains("ac infinity") or advert.name_starts_with("acinfinity")
    if named:
        evidence.append(f'advertised name "{advert.name}" matches AC Infinity naming')
    if raw is not None:
        return _Detection(FamilyConfidence.LIKELY, evidence)
    if named:
        return _Detection(FamilyConfidence.POSSIBLE, evidence)
    return None


FAMILIES: list[_Family] = [
    _Family(
        id="iledclock",
        name="iLedClock",
        detect=_detect_iledclock,
        driver_url=None,
        codec_id="iledclock",
        hints=["fff1"],
        confirm=_confirm_iledclock,
        starter_command_map=ILEDCLOCK_STARTER_COMMAND_MAP,
        dedicated_integration=ILEDCLOCK_DEDICATED_INTEGRATION,
    ),
    _Family(
        id="coolled",
        name="CoolLED (CoolLEDX / iLedClock)",
        detect=_detect_coolled,
        driver_url="https://github.com/UpDryTwist/coolledx-driver",
        codec_id="coolled",
        hints=["fff1"],
        confirm=_confirm_coolled,
    ),
    _Family(
        id="xiaomi-mibeacon",
        name="Xiaomi MiBeacon",
        detect=_detect_xiaomi_mibeacon,
        driver_url="https://github.com/Bluetooth-Devices/xiaomi-ble",
    ),
    _Family(
        id="govee",
        name="Govee",
        detect=_detect_govee,
        driver_url="https://github.com/Bluetooth-Devices/govee-ble",
        hints=["1911"],
        confirm=_confirm_govee,
    ),
    _Family(
        id="telink-mesh",
        name="Telink mesh",
        detect=_detect_telink_mesh,
        driver_url="https://github.com/mjg59/python-tikteck",
        hints=["1911"],
        confirm=_confirm_telink_mesh,
    ),
    _Family(
        id="tuya-ble",
        name="Tuya BLE",
        detect=_detect_tuya_ble,
        driver_url="https://github.com/PlusPlus-ua/ha_tuya_ble",
        hints=["a202"],
        confirm=_confirm_tuya_ble,
    ),
    _Family(
        id="nordic-uart",
        name="Nordic UART service",
        detect=_detect_nordic_uart,
        hints=["6e400002"],
        confirm=_confirm_nordic_uart,
    ),
    _Family(
        id="bedjet",
        name="BedJet",
        detect=_detect_bedjet,
        driver_url="https://www.home-assistant.io/integrations/bedjet/",
        hints=["2004"],
        confirm=_confirm_bedjet,
    ),
    _Family(
        id="ac-infinity",
        name="AC Infinity",
        detect=_detect_ac_infinity,
        driver_url="https://github.com/hunterjm/ac-infinity-ble",
    ),
]


# --- Generic command channels ------------------------------------------------

_SHORT_FORM = re.compile(r"0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb")


def _tail(u: str) -> str:
    """Short label for a uuid: the 16-bit alias if it has one, else its last 4 chars."""
    m = _SHORT_FORM.fullmatch(u)
    return m.group(1) if m else u[-4:]


def command_channel_matches(gatt: GattView) -> list[FamilyMatch]:
    """Describe every write/notify channel as a POSSIBLE, family-less match."""
    matches: list[FamilyMatch] = []
    for ch in gatt.command_channels():
        if ch.bidirectional:
            evidence = (
                f"GATT service {ch.service}: characteristic {ch.write} "
                f"{_property_list(ch.write_properties)} both takes writes and notifies "
                "- a command channel that answers on itself"
            )
        else:
            evidence = (
                f"GATT service {ch.service}: write characteristic {ch.write} "
                f"{_property_list(ch.write_properties)} paired with notify "
                f"characteristic {ch.notify} {_property_list(ch.notify_properties)}"
            )
        hints = [_tail(ch.write)] + ([] if ch.bidirectional else [_tail(ch.notify)])
        matches.append(
            FamilyMatch(
                family_id=f"command-channel:{_tail(ch.service)}",
                name=f"Command channel on {_tail(ch.service)}",
                confidence=FamilyConfidence.POSSIBLE,
                evidence=[evidence],
                public_driver_url=None,
                codec_id=None,
                command_characteristic_hints=hints,
            )
        )
    return matches


# --- Entry point -------------------------------------------------------------


def identify(input: FingerprintInput) -> list[FamilyMatch]:
    """Rank every family claim and generic command channel for one device.

    Ordering: CERTAIN before LIKELY before POSSIBLE, then more evidence first,
    then family id for a stable result.
    """
    advert = Advert(input)
    gatt = GattView(input.gatt)
    matches: list[FamilyMatch] = []
    for fam in FAMILIES:
        detection = fam.detect(advert)
        if detection is None:
            continue
        confirmation = fam.confirm(gatt)
        if confirmation is None:
            confidence = detection.confidence
            evidence = detection.evidence
        else:
            confidence = FamilyConfidence.CERTAIN
            evidence = [*detection.evidence, confirmation]
        matches.append(
            FamilyMatch(
                family_id=fam.id,
                name=fam.name,
                confidence=confidence,
                evidence=evidence,
                public_driver_url=fam.driver_url,
                codec_id=fam.codec_id,
                command_characteristic_hints=fam.hints,
                starter_command_map=fam.starter_command_map,
                dedicated_integration=fam.dedicated_integration,
            )
        )
    matches.extend(command_channel_matches(gatt))
    matches.sort(key=lambda m: (_CONFIDENCE_RANK[m.confidence], -len(m.evidence), m.family_id))
    return matches
