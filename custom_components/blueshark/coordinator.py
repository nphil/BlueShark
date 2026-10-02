"""Runtime device object for a guided-onboarding BlueShark config entry.

One `BlueSharkDevice` per guided config entry: owns the BLE transport, the chosen
codec, and connection/presence state, and exposes the encode-then-transport
helpers entities call to act. Update-by-listener, no polling - Home Assistant's
Bluetooth stack pushes presence changes, and every send updates `last_response`
before notifying listeners.

Also home to the address-keyed `BleTransport` registry that `transport.py`'s own
docstring documents ("looked up through :mod:`.runtime`"): a wizard session
running the onboarding prober (no config entry yet) and a fully configured
entry must never open two competing connections to the same physical device.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from dataclasses import dataclass

from .codecs import Codec
from .command_map import CommandMapError, encode_command, encode_switch_command
from .const import (
    CONF_ADDRESS,
    CONF_CHARACTERISTIC,
    CONF_COMMAND_MAP,
    CONF_FAMILY_ID,
    CONF_OPCODE_LOG,
    DATA_TRANSPORTS,
    DEFAULT_AWAIT_RESPONSE_MS,
    DOMAIN,
    MAX_OPCODE_LOG_ENTRIES,
)
from .sweep import verdict as _verdict

#: Mirrors `homeassistant.helpers.device_registry.CONNECTION_BLUETOOTH`.
CONNECTION_BLUETOOTH = "bluetooth"

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo

    from .transport import BleTransport, RawResponse

_LOGGER = logging.getLogger(__name__)

#: Upper bound for the shutdown-time release. Home Assistant gives all shutdown jobs one shared 20 s budget.
SHUTDOWN_RELEASE_TIMEOUT_S = 8


@dataclass(frozen=True)
class LastResponse:
    """The most recent request/response cycle, shaped for entity consumption.

    Returned verbatim by every `async_send_*` helper below, and mirrored onto
    `BlueSharkDevice.last_response` before listeners are notified - the two are
    always the same object for a given send.
    """

    key: str | None
    opcode: int | None
    sent_hex: str
    response_hex: str | None
    verdict: str
    status: int | None
    elapsed_ms: int
    at: float


# ---------------------------------------------------------------------------
# Shared transport registry: one BleTransport per address, regardless of caller.
# ---------------------------------------------------------------------------


def async_get_transport(hass: HomeAssistant, address: str, name: str) -> BleTransport:
    """Return the shared `BleTransport` for `address`, creating it on first use.

    Both the wizard's WS-API prober (before any config entry exists) and a
    guided entry's own `BlueSharkDevice` call this, so they always share one
    connection instead of racing each other for the device's single link.
    """
    from .transport import BleTransport

    registry: dict[str, BleTransport] = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_TRANSPORTS, {})
    transport = registry.get(address)
    if transport is None:
        transport = BleTransport(hass, address, name)
        registry[address] = transport
    return transport


async def async_release_transport(hass: HomeAssistant, address: str) -> None:
    """Disconnect and drop the shared transport for `address`, if one is cached."""

    registry = hass.data.get(DOMAIN, {}).get(DATA_TRANSPORTS)
    if not registry:
        return
    transport = registry.pop(address, None)
    if transport is not None:
        await transport.async_disconnect()


async def _async_release_transport_at_shutdown(transport: BleTransport, label: str) -> None:
    """Latch `transport` closed and drop its link; bounded, never raises, one log line."""

    started = time.monotonic()
    try:
        async with asyncio.timeout(SHUTDOWN_RELEASE_TIMEOUT_S):
            await transport.async_release_for_shutdown()
    except TimeoutError:
        _LOGGER.warning(
            "Timed out after %s s releasing the BLE link to %s at shutdown", SHUTDOWN_RELEASE_TIMEOUT_S, label
        )
    except Exception as err:  # noqa: BLE001 - a shutdown job must never raise
        _LOGGER.warning("Could not release the BLE link to %s at shutdown: %s", label, err)
    else:
        _LOGGER.info("Released BLE link to %s at shutdown in %.2f s", label, time.monotonic() - started)


async def async_release_unowned_transports_at_shutdown(hass: HomeAssistant) -> None:
    """Shutdown job for transports no guided entry owns (the onboarding wizard's probes).

    A wizard session connects before any config entry exists, so no entry's shutdown job
    covers its link; the 30 s idle timer would otherwise leave it open across a restart.
    Transports owned by a guided entry are released by that entry's own job, in parallel.
    """

    domain_data = hass.data.get(DOMAIN, {})
    owned = {runtime.transport for runtime in domain_data.values() if isinstance(runtime, BlueSharkDevice)}
    registry: dict[str, BleTransport] = domain_data.get(DATA_TRANSPORTS) or {}
    pending = [
        _async_release_transport_at_shutdown(transport, transport.name)
        for transport in list(registry.values())
        if transport not in owned
    ]
    if pending:
        await asyncio.gather(*pending)


# ---------------------------------------------------------------------------
# BlueSharkDevice
# ---------------------------------------------------------------------------


class BlueSharkDevice:
    """Owns one physical device's transport + codec + presence/connection state.

    Constructed once in `async_setup_entry` for a guided entry (one with
    `CONF_CHARACTERISTIC` in its data) and stored at
    `hass.data[DOMAIN][entry.entry_id]`. Entities read `available`, `connected`,
    `advertising`, `device_info`, `last_response` and `command_map`, call
    `async_send_command`/`async_send_switch`/`async_send_number`, and register
    for updates with `async_add_listener` - there is no polling anywhere here.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        transport: BleTransport,
        codec: Codec,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.transport = transport
        self.codec = codec
        self.address: str = str(entry.data[CONF_ADDRESS])
        self.name: str = entry.title or self.address
        self.family_id: str | None = entry.data.get(CONF_FAMILY_ID)
        self.last_response: LastResponse | None = None
        self.opcode_log: list[dict[str, Any]] = list(entry.options.get(CONF_OPCODE_LOG, []))
        self._listeners: list[Callable[[], None]] = []
        self._advertising = False
        self._unsub_advertisement: Callable[[], None] | None = None
        self._unsub_unavailable: Callable[[], None] | None = None
        transport.on_connection_changed = self._handle_connection_changed

    # ------------------------------------------------------------------ lifecycle

    def async_start(self) -> None:
        """Begin tracking this device's presence via Home Assistant's Bluetooth stack.

        Call once, from `async_setup_entry`, after the entry's platforms are set up.
        """
        from homeassistant.components import bluetooth

        self._unsub_advertisement = bluetooth.async_register_callback(
            self.hass,
            self._handle_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        self._unsub_unavailable = bluetooth.async_track_unavailable(
            self.hass, self._handle_unavailable, self.address, connectable=True
        )
        self._advertising = bluetooth.async_address_present(self.hass, self.address, connectable=True)

    async def async_stop(self) -> None:
        """Stop tracking presence and disconnect. Call from `async_unload_entry`."""

        if self._unsub_advertisement is not None:
            self._unsub_advertisement()
            self._unsub_advertisement = None
        if self._unsub_unavailable is not None:
            self._unsub_unavailable()
            self._unsub_unavailable = None
        await async_release_transport(self.hass, self.address)

    async def async_release_at_shutdown(self) -> None:
        """Home Assistant shutdown job (Stage 1, before Bluetooth and the proxies go away).

        Stops the presence watchers, latches the transport closed and drops its link. Bounded,
        never raises, and deliberately does NOT unload the entry (that would write a wave of
        ``unavailable`` states); the transport stays registered but can never connect again.
        """

        if self._unsub_advertisement is not None:
            self._unsub_advertisement()
            self._unsub_advertisement = None
        if self._unsub_unavailable is not None:
            self._unsub_unavailable()
            self._unsub_unavailable = None
        await _async_release_transport_at_shutdown(self.transport, self.name)

    # ------------------------------------------------------------------ presence/connection

    def _handle_advertisement(self, service_info: Any, change: Any) -> None:
        self._advertising = True
        self._notify_listeners()

    def _handle_unavailable(self, address: str) -> None:
        self._advertising = False
        self._notify_listeners()

    def _handle_connection_changed(self, connected: bool) -> None:
        self._notify_listeners()

    @property
    def connected(self) -> bool:
        """True only while a BLE connection is actually open right now.

        Flips to `False` the instant the idle-disconnect timer (or an unload)
        drops the link - this is *not* a proxy for reachability, since the
        transport disconnects between commands by design.
        """
        return self.transport.connected

    @property
    def advertising(self) -> bool:
        """True while Home Assistant has heard this address advertise recently.

        Independent of `connected`: a device can be advertising but not
        connected (the common case between commands), or briefly connected
        without a fresh advertisement having been seen since. This is the
        signal a presence/"seen" binary sensor should bind to.
        """
        return self._advertising

    @property
    def available(self) -> bool:
        """General entity availability: reachable right now, one way or another."""

        return self.connected or self._advertising

    @property
    def device_info(self) -> DeviceInfo:
        """Static device info, present even while disconnected."""

        # `dr.CONNECTION_BLUETOOTH` is the literal "bluetooth"; using the string keeps this
        # property - and therefore the pure entity tests that read it - importable without HA.
        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, self.address)},
            "name": self.name,
            "connections": {(CONNECTION_BLUETOOTH, self.address)},
        }
        if self.family_id:
            info["model"] = self.family_id
        return info  # type: ignore[return-value]

    @property
    def command_map(self) -> dict[str, dict[str, Any]]:
        """The entry's current validated command map (mutable via the options flow/WS)."""

        return self.entry.options.get(CONF_COMMAND_MAP, {})

    # ------------------------------------------------------------------ listeners

    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register `update_callback`, invoked with no args after any state change.

        Returns an unsubscribe callable; entities should pass it to
        `self.async_on_remove(...)`.
        """

        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------ sending

    def _command(self, key: str) -> dict[str, Any]:
        try:
            return self.command_map[key]
        except KeyError:
            raise CommandMapError(f"no command_map entry {key!r} for {self.address}") from None

    async def async_send_command(self, key: str) -> LastResponse:
        """Encode and send a `button`-kind command-map entry by key."""

        entry = self._command(key)
        payload, characteristic = encode_command(entry)
        return await self._async_send(key, payload, characteristic)

    async def async_send_switch(self, key: str, *, on: bool) -> LastResponse:
        """Encode and send a `switch`-kind command-map entry's on/off payload."""

        entry = self._command(key)
        payload, characteristic = encode_switch_command(entry, on=on)
        return await self._async_send(key, payload, characteristic)

    async def async_send_number(self, key: str, value: int) -> LastResponse:
        """Encode and send a `number`-kind command-map entry with `value`.

        Raises `CommandMapError` if `value` is outside the entry's min..max -
        never clamped.
        """

        entry = self._command(key)
        payload, characteristic = encode_command(entry, value=value)
        return await self._async_send(key, payload, characteristic)

    async def _async_send(self, key: str | None, payload: bytes, characteristic: str | None) -> LastResponse:
        target = characteristic or self.entry.data.get(CONF_CHARACTERISTIC)
        if not target:
            raise CommandMapError(f"{self.address} has no characteristic configured to send on")
        wire = self.codec.encode(payload)
        raw: RawResponse = await self.transport.request(target, wire, DEFAULT_AWAIT_RESPONSE_MS)
        verdict, status = _verdict(self.codec, raw.response)
        response = LastResponse(
            key=key,
            opcode=payload[0] if payload else None,
            sent_hex=raw.sent.hex(),
            response_hex=raw.response.hex() if raw.response is not None else None,
            verdict=verdict,
            status=status,
            elapsed_ms=raw.elapsed_ms,
            at=time.monotonic(),
        )
        self.last_response = response
        self.log_opcode(
            key=response.key,
            opcode=response.opcode,
            sent_hex=response.sent_hex,
            response_hex=response.response_hex,
            verdict=response.verdict,
            status=response.status,
            elapsed_ms=response.elapsed_ms,
            at=response.at,
        )
        self._notify_listeners()
        return response

    def log_opcode(
        self,
        *,
        key: str | None,
        opcode: int | None,
        sent_hex: str,
        response_hex: str | None,
        verdict: str,
        status: int | None,
        elapsed_ms: int,
        at: float,
    ) -> None:
        """Append one entry to the in-memory opcode log, trimmed to `MAX_OPCODE_LOG_ENTRIES`.

        In-memory only - never round-tripped through `entry.options` on every call, since that
        would fire the options-update listener and reload the whole entry on every single send.
        The options flow persists a snapshot of this into `entry.options[CONF_OPCODE_LOG]` when
        it saves, purely so a reload triggered by some *other* option change doesn't lose it.
        """

        self.opcode_log.append(
            {
                "at": at,
                "key": key,
                "opcode": opcode,
                "sent_hex": sent_hex,
                "response_hex": response_hex,
                "verdict": verdict,
                "status": status,
                "elapsed_ms": elapsed_ms,
            }
        )
        if len(self.opcode_log) > MAX_OPCODE_LOG_ENTRIES:
            del self.opcode_log[: len(self.opcode_log) - MAX_OPCODE_LOG_ENTRIES]
