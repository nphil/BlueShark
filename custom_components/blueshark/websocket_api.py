"""BlueShark WebSocket API: the panel's only way to talk to the engine.

Every command is `type: "blueshark/<name>"`, admin-only, and returns success
data or an error code from `{not_found, no_route, busy, refused, timeout,
unsupported}` with a human message - see `local://blueshark-ha-panel-contract.md`
for the full command list and payload shapes.

This module is split in two, deliberately:

- The top half (down to `async_register_commands`) is pure: stdlib plus
  `.codecs`/`.command_map`/`.families`/`.sweep`, never Home Assistant or bleak.
  Every WS response/event shape and every error-code decision is a small
  function here, so `tests/test_ws_logic.py` can cover them directly, in a
  sandbox with neither Home Assistant nor bleak installed.
- `async_register_commands` (and everything nested inside it) is the real HA
  glue: it imports `homeassistant`/`voluptuous` lazily, on call, so importing
  this module never requires them. `__init__.py` calls it once from
  `async_setup`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .codecs import Codec, get_codec
from .const import (
    WS_ERROR_BUSY,
    WS_ERROR_NO_ROUTE,
    WS_ERROR_REFUSED,
    WS_ERROR_TIMEOUT,
    WS_ERROR_UNSUPPORTED,
)
from .families import (
    COOLLED_MANUFACTURER,
    DedicatedIntegration,
    FamilyConfidence,
    FamilyMatch,
    FingerprintInput,
    GattCharacteristic,
    GattDatabase,
    GattService,
    GattView,
    canonical_uuid,
    decode_coolled_panel,
    decode_mibeacon,
    identify,
)
from .sweep import SweepCodec, SweepStep, interpret_sweep, verdict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# ---------------------------------------------------------------------------
# Pure data shapes and decision functions. No Home Assistant or bleak import
# above this line, ever: tests/test_ws_logic.py imports this module directly
# in a sandbox with neither installed.
# ---------------------------------------------------------------------------

_CONFIDENCE_SCORE: dict[FamilyConfidence, float] = {
    FamilyConfidence.CERTAIN: 1.0,
    FamilyConfidence.LIKELY: 0.7,
    FamilyConfidence.POSSIBLE: 0.4,
}


def confidence_score(confidence: FamilyConfidence) -> float:
    """Categorical family confidence -> a 0..1 score for the panel's confidence bar."""

    return _CONFIDENCE_SCORE[confidence]


_UNKNOWN_DESTRUCTIVE_REASON = "Unknown effect; blocked to be safe"


def safety_notes(codec: Codec | None) -> list[dict[str, Any]]:
    """`{opcode, reason}` for every opcode `codec` marks destructive, sorted by opcode.

    A destructive opcode missing from `codec.destructive_reasons` still gets a row here -
    "no reason recorded" must never read as "safe to send" - with a generic fallback reason.
    `codec` is `None` for a family-less command-channel match, which yields `[]`.
    """

    if codec is None:
        return []
    return [
        {"opcode": opcode, "reason": codec.destructive_reasons.get(opcode, _UNKNOWN_DESTRUCTIVE_REASON)}
        for opcode in sorted(codec.destructive_opcodes)
    ]


def _shape_dedicated_integration(integration: DedicatedIntegration | None) -> dict[str, str] | None:
    if integration is None:
        return None
    return {
        "domain": integration.domain,
        "name": integration.name,
        "url": integration.url,
        "summary": integration.summary,
    }


def shape_family_match(match: FamilyMatch) -> dict[str, Any]:
    """One `FamilyMatch` -> the wire shape shared by scan/identify/enumerate hints.

    Carries both a numeric `confidence` (0..1, for a bar/percentage) and the
    original `confidence_label` ("certain"|"likely"|"possible") side by side,
    so nothing about the family table's own evidence grading is lost.
    """

    codec = get_codec(match.codec_id) if match.codec_id else None
    return {
        "id": match.family_id,
        "name": match.name,
        "confidence": confidence_score(match.confidence),
        "confidence_label": match.confidence.value,
        "evidence": list(match.evidence),
        "codec_id": match.codec_id,
        "characteristic_hints": list(match.command_characteristic_hints),
        "driver_url": match.public_driver_url,
        "starter_command_map": dict(match.starter_command_map) if match.starter_command_map else None,
        "dedicated_integration": _shape_dedicated_integration(match.dedicated_integration),
        "safety": safety_notes(codec),
    }


@dataclass(frozen=True)
class AdvertisementSnapshot:
    """Plain-data mirror of the advertisement fields the panel needs.

    Deliberately not `homeassistant.components.bluetooth.BluetoothServiceInfoBleak`
    itself, so this module keeps importing - and being unit-testable - without
    Home Assistant installed. `async_register_commands` builds one of these
    from the real `BluetoothServiceInfoBleak` HA hands its callback.
    """

    address: str
    name: str | None
    rssi: int | None
    source: str | None
    connectable: bool
    service_uuids: list[str]
    manufacturer_data: dict[int, bytes]
    service_data: dict[str, bytes]


def decoded_facts(manufacturer_data: dict[int, bytes], service_data: dict[str, bytes]) -> dict[str, Any]:
    """Best-effort structured facts decoded from advertisement payloads.

    Used by `blueshark/identify`'s `decoded` field. Every fact here traces to a
    concrete decoder in `.families`; an advertisement nothing recognises yields
    an empty dict rather than a guess.
    """

    facts: dict[str, Any] = {}
    coolled_raw = manufacturer_data.get(COOLLED_MANUFACTURER)
    if coolled_raw is not None:
        panel = decode_coolled_panel(coolled_raw)
        if panel is not None:
            facts["coolled_panel"] = {
                "id_hex": panel.id_hex,
                "width": panel.width,
                "height": panel.height,
                "colour": panel.colour,
                "firmware": panel.firmware,
            }
    for uuid, data in service_data.items():
        canonical = canonical_uuid(uuid)
        if canonical is not None and "0000fe95-" in canonical:
            frame = decode_mibeacon(data)
            if frame is not None:
                facts["mibeacon"] = {
                    "frame_control": frame.frame_control,
                    "product_id": frame.product_id,
                    "counter": frame.counter,
                    "mac": frame.mac,
                    "encrypted": frame.encrypted,
                }
    return facts


def shape_scan_event(info: AdvertisementSnapshot) -> dict[str, Any]:
    """One advertisement -> a `blueshark/scan/subscribe` stream event."""

    matches = identify(
        FingerprintInput(
            name=info.name,
            service_uuids=info.service_uuids,
            manufacturer_data=info.manufacturer_data,
            service_data=info.service_data,
        )
    )
    return {
        "address": info.address,
        "name": info.name,
        "rssi": info.rssi,
        "source": info.source,
        "connectable": info.connectable,
        "service_uuids": list(info.service_uuids),
        "manufacturer_data": {str(mfg_id): data.hex() for mfg_id, data in info.manufacturer_data.items()},
        "family": shape_family_match(matches[0]) if matches else None,
    }


def shape_identify(
    matches: list[FamilyMatch], manufacturer_data: dict[int, bytes], service_data: dict[str, bytes]
) -> dict[str, Any]:
    """`blueshark/identify` response shape."""

    return {
        "matches": [shape_family_match(match) for match in matches],
        "decoded": decoded_facts(manufacturer_data, service_data),
    }


@dataclass(frozen=True)
class EnumeratedCharacteristic:
    """One GATT characteristic, as reported by `transport.enumerate_gatt()`."""

    uuid: str
    handle: int
    properties: list[str]


@dataclass(frozen=True)
class EnumeratedService:
    """One GATT service, as reported by `transport.enumerate_gatt()`."""

    uuid: str
    characteristics: list[EnumeratedCharacteristic]


def shape_enumerate(
    services: list[EnumeratedService], preferred_hints: list[str] | None = None, codec_id: str | None = None
) -> dict[str, Any]:
    """`blueshark/enumerate` response: the full GATT table plus one suggested channel.

    The suggested channel is the family's hinted write/notify pair when one of
    its hint fragments (e.g. "fff1") appears in a discovered channel's write or
    notify uuid, else simply the first write+notify channel found. `None` when
    the device exposes no channel that both takes writes and can answer.
    `codec_id` is the matched family's codec, when one matched, so the wizard
    can preselect it alongside the suggested channel.
    """

    gatt = GattDatabase(
        services=[
            GattService(
                uuid=service.uuid,
                characteristics=[
                    GattCharacteristic(uuid=char.uuid, properties=char.properties)
                    for char in service.characteristics
                ],
            )
            for service in services
        ]
    )
    channels = GattView(gatt).command_channels()
    suggested: dict[str, Any] | None = None
    if channels:
        best = channels[0]
        if preferred_hints:
            hinted = next(
                (ch for ch in channels if any(hint in ch.write or hint in ch.notify for hint in preferred_hints)),
                None,
            )
            best = hinted or best
        suggested = {"service": best.service, "characteristic": best.write, "codec_id": codec_id}
    return {
        "services": [
            {
                "uuid": service.uuid,
                "characteristics": [
                    {"uuid": char.uuid, "handle": char.handle, "properties": list(char.properties)}
                    for char in service.characteristics
                ],
            }
            for service in services
        ],
        "suggested": suggested,
    }


def encode_for_wire(codec: Codec, payload: bytes, framed: bool) -> bytes:
    """Apply codec framing, unless `framed` says `payload` is already on-wire bytes."""

    return bytes(payload) if framed else codec.encode(payload)


def send_result(sent: bytes, response: bytes | None, elapsed_ms: int, codec: SweepCodec) -> dict[str, Any]:
    """`blueshark/send` response shape (also used by the send_raw/probe_opcode services).

    `sent` is the framed bytes actually written to the characteristic; it is decoded back
    through `codec` to recover the unframed request payload, which lets a codec's `classify`
    hook (see `CoolLedCodec.classify`) recognise a reply that echoes the request instead of
    answering with a status byte. If `sent` doesn't decode - e.g. a caller wrote bytes the
    codec itself can't parse - the request is `None` and `verdict` falls back to its
    non-echo classification.
    """

    request = codec.decode(sent)
    v, status = verdict(codec, response, request)
    return {
        "sent_hex": sent.hex(),
        "response_hex": response.hex() if response is not None else None,
        "verdict": v,
        "status": status,
        "elapsed_ms": elapsed_ms,
    }


def sweep_progress_event(
    index: int, total: int, step: SweepStep, response: bytes | None, elapsed_ms: int, codec: SweepCodec
) -> dict[str, Any]:
    """One streamed `blueshark/sweep/start` progress event."""

    v, status = verdict(codec, response, step.payload)
    return {
        "index": index,
        "total": total,
        "opcode": step.opcode,
        "sent_hex": step.payload.hex(),
        "response_hex": response.hex() if response is not None else None,
        "verdict": v,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "canary": step.kind == "canary",
    }


def sweep_final_event(interpretation: dict[str, Any]) -> dict[str, Any]:
    """The final `{done: true, ...}` event a `blueshark/sweep/start` stream ends with."""

    return {
        "done": True,
        "accepted": list(interpretation["accepted"]),
        "unknown": list(interpretation["unknown"]),
        "no_response": list(interpretation["no_response"]),
        "aborted_reason": interpretation["message"] if interpretation["aborted"] else None,
    }


def opcode_log_tail(log: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The most recent `limit` opcode-log entries (oldest first) for `commands/get`."""

    if limit <= 0:
        return []
    return list(log[-limit:])


class DeviceNotFoundError(Exception):
    """No config entry (guided runtime) matches the requested address."""


class SweepRunNotFoundError(Exception):
    """`blueshark/sweep/stop` named a `run_id` that is not currently active."""


_ERROR_CODE_BY_EXC_NAME: dict[str, str] = {
    "BusyError": WS_ERROR_BUSY,
    "NoRouteError": WS_ERROR_NO_ROUTE,
    "UnsupportedOperationError": WS_ERROR_UNSUPPORTED,
    "UnknownCodecError": WS_ERROR_UNSUPPORTED,
    "TransportDisconnectedError": WS_ERROR_REFUSED,
    "TransportError": WS_ERROR_REFUSED,
    "CommandMapError": WS_ERROR_REFUSED,
    "ProfileValidationError": WS_ERROR_REFUSED,
    "ValueError": WS_ERROR_REFUSED,
    "DeviceNotFoundError": "not_found",
    "SweepRunNotFoundError": "not_found",
    "StepTimeoutError": WS_ERROR_TIMEOUT,
    "TimeoutError": WS_ERROR_TIMEOUT,
}


def error_code_for_exception(exc: BaseException) -> str:
    """Map any exception a handler might raise to a `WS_ERROR_*` code, by class name.

    Duck-typed on `type(exc).__mro__` names rather than `isinstance` checks
    against `transport.TransportError`, so this stays importable - and
    unit-testable - without bleak/Home Assistant installed. The real transport
    exceptions raised at runtime carry exactly these class names, so the
    mapping is exact, not approximate.
    """

    for klass in type(exc).__mro__:
        code = _ERROR_CODE_BY_EXC_NAME.get(klass.__name__)
        if code is not None:
            return code
    return WS_ERROR_REFUSED


def flow_id_to_adopt(address: str, in_progress: list[dict[str, Any]]) -> str | None:
    """Pick the in-progress config flow (if any) already claiming `address` as its unique id.

    Home Assistant's own bluetooth discovery starts a flow the moment it sees a new device
    advertise. If the panel's guided wizard then tries to create an entry for that same
    address, `ConfigFlow.async_set_unique_id` aborts with `already_in_progress` unless that
    earlier flow is cleared out of the way first. `in_progress` is
    `hass.config_entries.flow.async_progress_by_handler(DOMAIN)` - a plain list of mappings,
    so this stays pure and testable without Home Assistant. Returns the `flow_id` to abort via
    `hass.config_entries.flow.async_abort`, or `None` when nothing is in the way.
    """

    normalized = address.strip().lower()
    for flow in in_progress:
        context = flow.get("context") or {}
        unique_id = context.get("unique_id")
        if isinstance(unique_id, str) and unique_id.strip().lower() == normalized:
            return flow.get("flow_id")
    return None


# ---------------------------------------------------------------------------
# Real registration. Only ever called from `__init__.py`'s `async_setup`, at
# real Home Assistant runtime - never imported or invoked by pure unit tests.
# ---------------------------------------------------------------------------


def async_register_commands(hass: HomeAssistant) -> None:
    """Register every `blueshark/*` WebSocket command. Idempotent-safe to call once."""

    import voluptuous as vol
    from homeassistant.components import bluetooth, websocket_api
    from homeassistant.const import CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import config_validation as cv

    from . import coordinator
    from .codecs import get_codec
    from .command_map import CommandMapError, validate_command_map
    from .const import (
        CONF_ADDRESS,
        CONF_CHARACTERISTIC,
        CONF_CODEC_ID,
        CONF_CODEC_PARAMS,
        CONF_COMMAND_MAP,
        CONF_OPCODE_LOG,
        DEFAULT_AWAIT_RESPONSE_MS,
        DEFAULT_SWEEP_END,
        DEFAULT_SWEEP_START,
        DEFAULT_SWEEP_STEP_DELAY_MS,
        CANARY_INTERVAL,
        DATA_SWEEP_RUNS,
        DOMAIN,
        OPCODE_LOG_TAIL_DISPLAY,
        SOURCE_PANEL,
        WS_ERROR_NOT_FOUND,
        WS_ERROR_REFUSED,
        WS_PREFIX,
    )
    from .sweep import plan_sweep

    def _find_entry(address: str):
        normalized = address.strip().lower()
        for entry in hass.config_entries.async_entries(DOMAIN):
            if (entry.unique_id or "").lower() == normalized:
                return entry
        return None

    def _resolve_codec(address: str, codec_id: str) -> Codec:
        entry = _find_entry(address)
        if entry is not None and entry.data.get(CONF_CODEC_ID) == codec_id:
            return get_codec(codec_id, entry.data.get(CONF_CODEC_PARAMS))
        return get_codec(codec_id)

    def _snapshot(info: Any) -> AdvertisementSnapshot:
        return AdvertisementSnapshot(
            address=info.address,
            name=info.name,
            rssi=info.rssi,
            source=info.source,
            connectable=bool(info.connectable),
            service_uuids=list(info.service_uuids),
            manufacturer_data=dict(info.manufacturer_data),
            service_data=dict(info.service_data),
        )

    def _identify_from_cache(address: str, *, connectable: bool | None):
        """Return `(info, matches)` for the freshest known advertisement, or `(None, [])`."""

        info = bluetooth.async_last_service_info(hass, address, connectable=connectable)
        if info is None:
            return None, []
        snapshot = _snapshot(info)
        matches = identify(
            FingerprintInput(
                name=snapshot.name,
                service_uuids=snapshot.service_uuids,
                manufacturer_data=snapshot.manufacturer_data,
                service_data=snapshot.service_data,
            )
        )
        return snapshot, matches

    # ---- blueshark/scan/subscribe ----

    @websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}/scan/subscribe"})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_scan_subscribe(hass, connection, msg):
        def _handle(service_info: Any, change: Any = None) -> None:
            connection.send_message(
                websocket_api.event_message(msg["id"], shape_scan_event(_snapshot(service_info)))
            )

        cancel = bluetooth.async_register_callback(
            hass,
            _handle,
            bluetooth.BluetoothCallbackMatcher(connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        connection.subscriptions[msg["id"]] = cancel
        connection.send_result(msg["id"])
        # Replay everything already known so the panel doesn't start from empty.
        for service_info in bluetooth.async_discovered_service_info(hass, connectable=True):
            _handle(service_info)

    # ---- blueshark/identify ----

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{WS_PREFIX}/identify", vol.Required("address"): cv.string}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_identify(hass, connection, msg):
        info, matches = _identify_from_cache(msg["address"], connectable=None)
        if info is None:
            connection.send_error(
                msg["id"], WS_ERROR_NOT_FOUND, f"no advertisement seen recently for {msg['address']}"
            )
            return
        connection.send_result(msg["id"], shape_identify(matches, info.manufacturer_data, info.service_data))

    # ---- blueshark/enumerate ----

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{WS_PREFIX}/enumerate", vol.Required("address"): cv.string}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_enumerate(hass, connection, msg):
        address = msg["address"]
        info, matches = _identify_from_cache(address, connectable=True)
        hints = list(matches[0].command_characteristic_hints) if matches else []
        codec_id = matches[0].codec_id if matches else None
        name = info.name if info is not None and info.name else address
        transport = coordinator.async_get_transport(hass, address, name)
        try:
            raw_services = await transport.enumerate_gatt()
        except Exception as err:  # noqa: BLE001 - mapped below, transport's exact types are HA-only
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        services = [
            EnumeratedService(
                uuid=service.uuid,
                characteristics=[
                    EnumeratedCharacteristic(uuid=char.uuid, handle=char.handle, properties=list(char.properties))
                    for char in service.characteristics
                ],
            )
            for service in raw_services
        ]
        connection.send_result(msg["id"], shape_enumerate(services, hints, codec_id))

    # ---- blueshark/send ----

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{WS_PREFIX}/send",
            vol.Required("address"): cv.string,
            vol.Required("characteristic"): cv.string,
            vol.Required("payload_hex"): cv.string,
            vol.Required("codec_id"): cv.string,
            vol.Optional("framed", default=False): cv.boolean,
            vol.Optional("await_response_ms", default=DEFAULT_AWAIT_RESPONSE_MS): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_send(hass, connection, msg):
        try:
            payload = bytes.fromhex(msg["payload_hex"])
        except ValueError as err:
            connection.send_error(msg["id"], WS_ERROR_REFUSED, f"payload_hex is not valid hex: {err}")
            return
        try:
            codec = _resolve_codec(msg["address"], msg["codec_id"])
        except Exception as err:  # noqa: BLE001 - UnknownCodecError et al, mapped below
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        wire = encode_for_wire(codec, payload, msg["framed"])
        transport = coordinator.async_get_transport(hass, msg["address"], msg["address"])
        try:
            raw = await transport.request(msg["characteristic"], wire, msg["await_response_ms"])
        except Exception as err:  # noqa: BLE001 - transport's exact types are HA-only
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        connection.send_result(msg["id"], send_result(raw.sent, raw.response, raw.elapsed_ms, codec))

    # ---- blueshark/sweep/start, blueshark/sweep/stop ----

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{WS_PREFIX}/sweep/start",
            vol.Required("address"): cv.string,
            vol.Required("characteristic"): cv.string,
            vol.Required("codec_id"): cv.string,
            vol.Optional("start", default=DEFAULT_SWEEP_START): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional("end", default=DEFAULT_SWEEP_END): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional("argument_hex", default=""): cv.string,
            vol.Optional("include_destructive", default=False): cv.boolean,
            vol.Optional("step_delay_ms", default=DEFAULT_SWEEP_STEP_DELAY_MS): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional("await_response_ms", default=DEFAULT_AWAIT_RESPONSE_MS): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_sweep_start(hass, connection, msg):
        import asyncio
        import uuid as uuid_mod

        try:
            codec = _resolve_codec(msg["address"], msg["codec_id"])
            argument = bytes.fromhex(msg["argument_hex"])
        except Exception as err:  # noqa: BLE001
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        try:
            steps = plan_sweep(
                msg["start"], msg["end"], argument, msg["include_destructive"], codec, CANARY_INTERVAL
            )
        except ValueError as err:
            connection.send_error(msg["id"], WS_ERROR_REFUSED, str(err))
            return

        run_id = uuid_mod.uuid4().hex
        cancel_event = asyncio.Event()
        runs: dict[str, Any] = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_SWEEP_RUNS, {})
        runs[run_id] = cancel_event
        connection.send_result(msg["id"])
        # `run_id` cannot travel as this command's `result`: the panel calls this through
        # `hass.connection.subscribeMessage`, whose returned promise resolves to an unsubscribe
        # function only - the initial result payload is parsed and discarded (see
        # home-assistant-js-websocket's `Connection.subscribeMessage`). It is instead the first
        # streamed event, ahead of every progress event.
        connection.send_message(websocket_api.event_message(msg["id"], {"run_id": run_id}))

        transport = coordinator.async_get_transport(hass, msg["address"], msg["address"])
        step_results: list[tuple[Any, bytes | None]] = []
        try:
            total = len(steps)
            for index, step in enumerate(steps, start=1):
                if cancel_event.is_set():
                    break
                if index > 1:
                    await asyncio.sleep(msg["step_delay_ms"] / 1000)
                try:
                    raw = await transport.request(
                        msg["characteristic"], codec.encode(step.payload), msg["await_response_ms"]
                    )
                except Exception as err:  # noqa: BLE001
                    connection.send_message(
                        websocket_api.event_message(
                            msg["id"], {"error": {"code": error_code_for_exception(err), "message": str(err)}}
                        )
                    )
                    return
                step_results.append((step, raw.response))
                connection.send_message(
                    websocket_api.event_message(
                        msg["id"], sweep_progress_event(index, total, step, raw.response, raw.elapsed_ms, codec)
                    )
                )
            interpretation = interpret_sweep(codec, step_results)
            connection.send_message(websocket_api.event_message(msg["id"], sweep_final_event(interpretation)))
        finally:
            runs.pop(run_id, None)

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{WS_PREFIX}/sweep/stop", vol.Required("run_id"): cv.string}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_sweep_stop(hass, connection, msg):
        runs = hass.data.get(DOMAIN, {}).get(DATA_SWEEP_RUNS, {})
        cancel_event = runs.get(msg["run_id"])
        if cancel_event is None:
            connection.send_error(msg["id"], WS_ERROR_NOT_FOUND, f"no active sweep run {msg['run_id']}")
            return
        cancel_event.set()
        connection.send_result(msg["id"], {})

    # ---- blueshark/listen ----

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{WS_PREFIX}/listen",
            vol.Required("address"): cv.string,
            vol.Required("characteristic"): cv.string,
            vol.Required("seconds"): vol.All(vol.Coerce(float), vol.Range(min=0, max=600)),
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_listen(hass, connection, msg):
        transport = coordinator.async_get_transport(hass, msg["address"], msg["address"])

        def _on_frame(at: float, payload: bytes) -> None:
            connection.send_message(
                websocket_api.event_message(msg["id"], {"at_ms": int(at * 1000), "hex": payload.hex()})
            )

        try:
            await transport.listen(msg["characteristic"], msg["seconds"], _on_frame)
        except Exception as err:  # noqa: BLE001
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        connection.send_message(websocket_api.event_message(msg["id"], {"done": True}))

    # ---- blueshark/commands/get, blueshark/commands/set ----

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{WS_PREFIX}/commands/get", vol.Required("address"): cv.string}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_commands_get(hass, connection, msg):
        entry = _find_entry(msg["address"])
        if entry is None:
            connection.send_error(msg["id"], WS_ERROR_NOT_FOUND, f"no configured device at {msg['address']}")
            return
        log = entry.options.get(CONF_OPCODE_LOG, [])
        connection.send_result(
            msg["id"],
            {
                "command_map": entry.options.get(CONF_COMMAND_MAP, {}),
                "opcode_log_tail": opcode_log_tail(log, OPCODE_LOG_TAIL_DISPLAY),
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{WS_PREFIX}/commands/set",
            vol.Required("address"): cv.string,
            vol.Required("command_map"): dict,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_commands_set(hass, connection, msg):
        entry = _find_entry(msg["address"])
        if entry is None:
            connection.send_error(msg["id"], WS_ERROR_NOT_FOUND, f"no configured device at {msg['address']}")
            return
        try:
            normalized = validate_command_map(msg["command_map"])
        except CommandMapError as err:
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_COMMAND_MAP: normalized})
        connection.send_result(msg["id"], {})

    # ---- blueshark/create_entry ----

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{WS_PREFIX}/create_entry",
            vol.Required("address"): cv.string,
            vol.Required("name"): cv.string,
            vol.Required("codec_id"): cv.string,
            vol.Required("characteristic"): cv.string,
            vol.Optional("command_map", default=dict): dict,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_create_entry(hass, connection, msg):
        try:
            normalized_map = validate_command_map(msg["command_map"])
        except CommandMapError as err:
            connection.send_error(msg["id"], error_code_for_exception(err), str(err))
            return
        in_progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        adopted_flow_id = flow_id_to_adopt(msg["address"], in_progress)
        if adopted_flow_id is not None:
            hass.config_entries.flow.async_abort(adopted_flow_id)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_PANEL},
            data={
                CONF_ADDRESS: msg["address"],
                CONF_NAME: msg["name"],
                CONF_CODEC_ID: msg["codec_id"],
                CONF_CHARACTERISTIC: msg["characteristic"],
                CONF_COMMAND_MAP: normalized_map,
            },
        )
        if result["type"] != FlowResultType.CREATE_ENTRY:
            connection.send_error(
                msg["id"], WS_ERROR_REFUSED, result.get("reason") or "could not create the device entry"
            )
            return
        connection.send_result(
            msg["id"],
            {"entry_id": result["result"].entry_id, "adopted_discovery": adopted_flow_id is not None},
        )

    for handler in (
        ws_scan_subscribe,
        ws_identify,
        ws_enumerate,
        ws_send,
        ws_sweep_start,
        ws_sweep_stop,
        ws_listen,
        ws_commands_get,
        ws_commands_set,
        ws_create_entry,
    ):
        websocket_api.async_register_command(hass, handler)
