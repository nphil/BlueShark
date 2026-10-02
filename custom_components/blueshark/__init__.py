"""BlueShark integration entry points.

Two config entry shapes share this file:

- Legacy profile-import entries (`CONF_PROFILE` in `entry.data`): unchanged
  behaviour, kept exactly as before so the Android app's exported profiles
  keep installing.
- Guided onboarding entries (`CONF_CHARACTERISTIC` in `entry.data`): built by
  the config flow's guided steps or by the panel's `blueshark/create_entry` WS
  command. `_async_setup_guided_entry` builds a `BlueSharkDevice` runtime
  (transport + coordinator + codec) and stores it at the same
  `hass.data[DOMAIN][entry.entry_id]` slot the legacy path uses for its dict.

`async_setup` registers the WS API, the services, and the onboarding panel
exactly once, regardless of how many entries (zero or more) exist - the panel
is how the *first* entry gets created, so none of this can depend on one
already existing.

Every import of anything that itself needs Home Assistant (`coordinator`,
`websocket_api`, `services`, `frontend`) is deferred to inside the functions
that use it, so this module - the package's own `__init__.py`, executed by
every `from custom_components.blueshark.<module> import ...` - stays
importable without Home Assistant installed, exactly like before.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .const import (
    HUB_UNIQUE_ID,
    CONF_ADDRESS,
    CONF_ALLOW_WRITES,
    CONF_CHARACTERISTIC,
    CONF_CODEC_ID,
    CONF_CODEC_PARAMS,
    CONF_IDLE_DISCONNECT_S,
    CONF_PROFILE,
    DEFAULT_IDLE_DISCONNECT_S,
    DOMAIN,
    PANEL_ICON,
    PANEL_JS_MODULE,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PLATFORMS,
)
from .profile import ProfileValidationError, parse_profile

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_PANEL_DIR = Path(__file__).parent / "panel"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the WS API, the services, and the onboarding panel.

    Runs once at component setup, before any config entry (guided or legacy)
    is loaded - the panel is how the first guided entry gets created at all.
    """

    hass.data.setdefault(DOMAIN, {})

    from homeassistant.core import HassJob

    from .coordinator import async_release_unowned_transports_at_shutdown

    # The onboarding wizard connects before any config entry exists; no entry's own shutdown
    # job covers that link, so one domain-level job releases transports no entry owns.
    hass.async_add_shutdown_job(
        HassJob(async_release_unowned_transports_at_shutdown, "blueshark release wizard BLE links"), hass
    )

    from . import websocket_api
    from .services import async_register_services

    websocket_api.async_register_commands(hass)
    async_register_services(hass)

    from homeassistant.components import frontend

    # HA 2024.7+ takes a list of StaticPathConfig; the singular helper was removed.
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_URL, str(_PANEL_DIR), False)]
    )
    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        require_admin=True,
        config={
            "_panel_custom": {
                "name": "blueshark-panel",
                "module_url": f"{PANEL_STATIC_URL}/{PANEL_JS_MODULE}",
                "embed_iframe": False,
                "trust_external": False,
            }
        },
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a legacy profile-import entry or a guided onboarding entry."""

    hass.data.setdefault(DOMAIN, {})
    if entry.unique_id == HUB_UNIQUE_ID or not entry.data:
        # The panel is registered in async_setup; this entry exists purely to trigger it.
        return True
    if CONF_CHARACTERISTIC in entry.data:
        return await _async_setup_guided_entry(hass, entry)
    return await _async_setup_legacy_entry(hass, entry)


async def _async_setup_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Validate the stored evidence before creating disabled button entities."""

    try:
        profile = parse_profile(entry.data[CONF_PROFILE])
    except (KeyError, ProfileValidationError) as err:
        _LOGGER.error("Refusing invalid BLE command profile: %s", err)
        return False
    hass.data[DOMAIN][entry.entry_id] = {
        "address": entry.data[CONF_ADDRESS],
        "allow_writes": bool(entry.data.get(CONF_ALLOW_WRITES, False)),
        "profile": profile,
        "write_lock": None,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_guided_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Build the device runtime (transport + coordinator + codec) and forward platforms."""

    from homeassistant.exceptions import ConfigEntryNotReady

    from . import shutdown
    from .codecs import get_codec
    from .coordinator import BlueSharkDevice, async_get_transport

    # Setup and reload during Home Assistant's shutdown must not start anything: an entry set up now
    # registers its shutdown job too late (the job list is read once). There is no `await` between
    # this check and the job registration below, so it cannot be invalidated in between.
    if shutdown.in_progress(hass):
        raise ConfigEntryNotReady("Home Assistant is shutting down")

    address = str(entry.data[CONF_ADDRESS])
    transport = async_get_transport(hass, address, entry.title or address)
    transport.set_idle_disconnect_s(int(entry.options.get(CONF_IDLE_DISCONNECT_S, DEFAULT_IDLE_DISCONNECT_S)))
    codec = get_codec(entry.data[CONF_CODEC_ID], entry.data.get(CONF_CODEC_PARAMS))

    device = BlueSharkDevice(hass, entry, transport, codec)
    hass.data[DOMAIN][entry.entry_id] = device
    device.async_start()

    from homeassistant.core import HassJob

    entry.async_on_unload(
        hass.async_add_shutdown_job(
            HassJob(device.async_release_at_shutdown, f"blueshark release BLE link {device.name}")
        )
    )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed (options flow, or the WS API's `commands/set`) - reload the entry."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry, disconnecting and dropping a guided entry's transport."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        from .coordinator import BlueSharkDevice

        if isinstance(runtime, BlueSharkDevice):
            await runtime.async_stop()
    return unloaded
