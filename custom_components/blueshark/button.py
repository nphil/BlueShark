"""Fixed, reviewed BLE GATT command buttons.

This platform intentionally does not discover commands or write on setup.
Only non-synthetic commands marked ``stage: tested`` are represented, and a
user must explicitly enable the integration's ``allow_writes`` setting before
pressing a button can perform a write.
"""

from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import shutdown
from .const import DOMAIN, WRITE_TIMEOUT
from .entity_specs import (
    CommandSpec,
    RuntimeAvailableMixin,
    RuntimeListenerMixin,
    build_button_specs,
    build_legacy_button_specs,
    is_legacy_runtime,
)

_LOGGER = logging.getLogger(__name__)

_SHUTTING_DOWN_MESSAGE = "Home Assistant is shutting down; not opening a Bluetooth connection"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Add legacy profile buttons and/or guided command-map buttons for this entry."""

    runtime = hass.data[DOMAIN][entry.entry_id]
    if is_legacy_runtime(runtime):
        # One lock is shared by every command for this physical device.
        runtime["write_lock"] = asyncio.Lock()
        entities = [
            BleCommandButton(
                hass=hass,
                entry=entry,
                address=runtime["address"],
                allow_writes=runtime["allow_writes"],
                write_lock=runtime["write_lock"],
                command=command,
                device_name=runtime["profile"]["device"]["name"],
            )
            for command in build_legacy_button_specs(runtime["profile"])
        ]
    else:
        command_map = getattr(runtime, "command_map", None)
        entities = [
            BlueSharkCommandButton(runtime, entry, spec)
            for spec in build_button_specs(command_map)
        ]
    if entities:
        async_add_entities(entities)


class BleCommandButton(ButtonEntity):
    """A single fixed GATT write from a validated evidence record."""

    _attr_entity_registry_enabled_default = False
    _attr_should_poll = False
    _attr_icon = "mdi:bluetooth"

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        allow_writes: bool,
        write_lock: asyncio.Lock,
        command: dict[str, object],
        device_name: str,
    ) -> None:
        self._entry = entry
        self._hass = hass
        self._address = address
        self._allow_writes = allow_writes
        self._write_lock = write_lock
        self._command = command
        self._attr_extra_state_attributes = {
            "transport": "Home Assistant shared Bluetooth stack (local adapter or active proxy)",
        }
        self._attr_name = str(command["name"])
        self._attr_unique_id = f"{address.lower()}_{command['id']}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address.lower())},
            "name": device_name,
        }

    @property
    def available(self) -> bool:
        """Remain retryable while exposing transport failures in attributes."""

        # Bluetooth resolution is intentionally done only on press. Keeping
        # this true avoids stranding the entity after a transient proxy slot
        # contention; the reason is surfaced in extra state attributes.
        return True

    def _refuse_if_shutting_down(self) -> None:
        if shutdown.in_progress(self._hass):
            raise HomeAssistantError(_SHUTTING_DOWN_MESSAGE)

    async def async_press(self) -> None:
        """Write exactly one reviewed payload, with one timeout and no retries."""

        if not self._allow_writes:
            raise HomeAssistantError(
                "BLE writes are disabled; reconfigure with allow_writes enabled"
            )
        payload = bytes.fromhex(str(self._command["value"]))
        self._refuse_if_shutting_down()
        async with self._write_lock:
            # Re-checked after waiting for the lock: shutdown may have begun while another press ran.
            self._refuse_if_shutting_down()
            try:
                async with asyncio.timeout(WRITE_TIMEOUT):
                    # HA's shared Bluetooth stack supports both local adapters
                    # and ESPHome proxies. connectable=True is intentional:
                    # passive/non-active proxies cannot execute GATT writes.
                    # Despite its historical async_ name, this HA helper is a
                    # synchronous callback returning BLEDevice | None.
                    device = bluetooth.async_ble_device_from_address(
                        self._hass, self._address, connectable=True
                    )
                    if device is None:
                        self._attr_extra_state_attributes["availability_reason"] = (
                            "No connectable device. Passive/non-active proxies cannot "
                            "perform GATT writes; an active proxy may be occupied."
                        )
                        self.async_write_ha_state()
                        raise HomeAssistantError(
                            "No connectable BLE device is available for this address. "
                            "A passive/non-active proxy cannot perform a GATT write; "
                            "a proxy may also be busy with another connection."
                        )
                    # HA's connector handles the shared-stack connection
                    # (including active ESPHome proxies); this does not retry
                    # or replay the command write itself.
                    client = await establish_connection(
                        BleakClientWithServiceCache,
                        device,
                        self._attr_name,
                    )
                    # No `await` between the connect returning and registering the client, so a
                    # shutdown job always sees a link that is open. The domain job drops it.
                    shutdown.track_client(self._hass, client)
                    try:
                        if shutdown.in_progress(self._hass):
                            # Shutdown began while the connect was in flight: hand the link back.
                            raise HomeAssistantError(_SHUTTING_DOWN_MESSAGE)
                        service = client.services.get_service(str(self._command["service"]))
                        if service is None:
                            raise HomeAssistantError(
                                "Profile service UUID was not exposed by the connected device"
                            )
                        characteristic = service.get_characteristic(
                            str(self._command["characteristic"])
                        )
                        if characteristic is None:
                            raise HomeAssistantError(
                                "Profile characteristic is not part of the mapped service"
                            )
                        required_property = (
                            "write" if bool(self._command["response"]) else "write-without-response"
                        )
                        properties = {
                            str(prop).lower() for prop in characteristic.properties
                        }
                        if required_property not in properties:
                            raise HomeAssistantError(
                                f"Characteristic does not support {required_property}"
                            )
                        if not self._command["response"] and len(payload) > characteristic.max_write_without_response_size:
                            raise HomeAssistantError("Payload exceeds the connection's write-without-response limit; automatic splitting is disabled")
                        await client.write_gatt_char(
                            characteristic,
                            payload,
                            response=bool(self._command["response"]),
                        )
                    finally:
                        try:
                            await client.disconnect()
                        finally:
                            shutdown.untrack_client(self._hass, client)
                self._attr_extra_state_attributes.pop("availability_reason", None)
                self.async_write_ha_state()
            except asyncio.TimeoutError as err:
                self._attr_extra_state_attributes["availability_reason"] = (
                    "Connection/write timed out; an active proxy may be occupied or "
                    "the device may not be connectable."
                )
                self.async_write_ha_state()
                raise HomeAssistantError("BLE command timed out") from err
            except BleakError as err:
                if shutdown.in_progress(self._hass):
                    # The shutdown job dropped this link under the press; not a fault of the device.
                    raise HomeAssistantError(_SHUTTING_DOWN_MESSAGE) from err
                raise HomeAssistantError(f"BLE connection/write failed: {err}") from err


class BlueSharkCommandButton(RuntimeAvailableMixin, RuntimeListenerMixin, ButtonEntity):
    """A single guided-onboarding command-map ``button`` entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: object, entry: ConfigEntry, spec: CommandSpec) -> None:
        self._runtime = runtime
        self._spec = spec
        self._attr_name = spec.name
        self._attr_unique_id = spec.unique_id(entry.entry_id, "button")
        device_info = getattr(runtime, "device_info", None)
        if device_info is not None:
            self._attr_device_info = device_info
        note = spec.entry.get("note")
        if note:
            self._attr_extra_state_attributes = {"note": note}

    async def async_press(self) -> None:
        try:
            await self._runtime.async_send_command(self._spec.key)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
