"""Startup contract: setup never waits on the radio, and no single BLE step can hang for ever.

(a) setup returns within budget while the device never answers; (b) presence arriving after
setup populates availability and wakes entities; (c) coming back from unavailable actuates
nothing; (d) a connect / subscribe / write that never completes fails fast with a step timeout.
Needs Home Assistant + bleak like test_shutdown_release.py; skipped otherwise.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

HAVE_HA = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "bleak", "bleak_retry_connector")
)

if HAVE_HA:
    from custom_components.blueshark import _async_setup_guided_entry
    from custom_components.blueshark import transport as transport_module
    from custom_components.blueshark.codecs import get_codec
    from custom_components.blueshark.const import CONF_ADDRESS, CONF_CHARACTERISTIC, CONF_CODEC_ID
    from custom_components.blueshark.coordinator import BlueSharkDevice, async_get_transport
    from custom_components.blueshark.transport import StepTimeoutError

ADDRESS = "AA:BB:CC:DD:EE:FF"
CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"


class _HangingGatt:
    """A connected client whose chosen GATT step never completes."""

    def __init__(self, hang: str) -> None:
        self.hang = hang
        self.is_connected = True
        self.disconnected = False
        self.services = SimpleNamespace(
            get_characteristic=lambda _u: SimpleNamespace(properties=["write-without-response"])
        )

    async def start_notify(self, *_a, **_k) -> None:
        if self.hang == "notify":
            await asyncio.sleep(3600)

    async def write_gatt_char(self, *_a, **_k) -> None:
        if self.hang == "write":
            await asyncio.sleep(3600)

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


@unittest.skipUnless(HAVE_HA, "needs homeassistant, bleak and bleak-retry-connector")
class StartupBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for started in (
            patch.object(transport_module, "STEP_TIMEOUT_S", 0.05),
            patch.object(
                transport_module.bluetooth,
                "async_ble_device_from_address",
                return_value=SimpleNamespace(address=ADDRESS, name="dev"),
            ),
            patch.object(transport_module, "async_call_later", return_value=MagicMock()),
        ):
            started.start()
            self.addCleanup(started.stop)
        self.hass = SimpleNamespace(data={})

    def _entry(self) -> MagicMock:
        entry = MagicMock()
        entry.title = "Fan"
        entry.data = {CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR, CONF_CODEC_ID: "raw"}
        entry.options = {}
        return entry

    # -- (a) setup never waits on the radio --------------------------------------------------

    async def test_setup_returns_at_once_while_the_device_never_answers(self) -> None:
        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        with (
            patch.object(transport_module, "establish_connection", side_effect=_never) as connect,
            patch.object(BlueSharkDevice, "async_start"),
        ):
            started = time.monotonic()
            async with asyncio.timeout(1):
                self.assertTrue(await _async_setup_guided_entry(hass, self._entry()))
            self.assertLess(time.monotonic() - started, 1)
            connect.assert_not_called()  # setup does not touch the radio at all

    # -- (b) data arriving after setup populates entities ------------------------------------

    async def test_presence_arriving_after_setup_makes_the_device_available_and_notifies(self) -> None:
        transport = async_get_transport(self.hass, ADDRESS, "Fan")
        device = BlueSharkDevice(self.hass, self._entry(), transport, get_codec("raw", {}))
        listener = MagicMock()
        device.async_add_listener(listener)
        self.assertFalse(device.available)  # no data yet: unavailable, nothing fabricated
        device._handle_advertisement(None, None)
        self.assertTrue(device.available)
        listener.assert_called_once_with()

    # -- (c) back from unavailable must not actuate ------------------------------------------

    async def test_coming_back_from_unavailable_sends_nothing(self) -> None:
        transport = async_get_transport(self.hass, ADDRESS, "Fan")
        device = BlueSharkDevice(self.hass, self._entry(), transport, get_codec("raw", {}))
        device._handle_unavailable(ADDRESS)
        with patch.object(transport_module.BleTransport, "request", new=AsyncMock()) as request:
            device._handle_advertisement(None, None)
            device._handle_connection_changed(True)
            await asyncio.sleep(0)
        request.assert_not_awaited()

    # -- (d) stuck steps fail fast ------------------------------------------------------------

    async def test_hanging_connect_fails_fast(self) -> None:
        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        transport = async_get_transport(self.hass, ADDRESS, "Fan")
        with patch.object(transport_module, "establish_connection", side_effect=_never):
            async with asyncio.timeout(2):
                with self.assertRaises(StepTimeoutError):
                    await transport.request(CHAR, b"\x01", 10)
        self.assertFalse(transport.is_busy())  # the lock is released for the next attempt

    async def test_hanging_subscribe_and_write_drop_the_link_and_fail_fast(self) -> None:
        for hang in ("notify", "write"):
            with self.subTest(hang=hang):
                hass = SimpleNamespace(data={})
                transport = async_get_transport(hass, ADDRESS, "Fan")
                gatt = _HangingGatt(hang)

                async def _establish(*_a, _gatt=gatt, **_k):
                    return _gatt

                with patch.object(transport_module, "establish_connection", side_effect=_establish):
                    async with asyncio.timeout(2):
                        with self.assertRaises(StepTimeoutError):
                            await transport.request(CHAR, b"\x01", 10)
                self.assertTrue(gatt.disconnected)  # the next attempt starts from a clean link
                self.assertFalse(transport.connected)
                self.assertFalse(transport.is_busy())


if __name__ == "__main__":
    unittest.main()
