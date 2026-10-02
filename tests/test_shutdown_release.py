"""Home Assistant shutdown releases the BLE link (Stage-1 shutdown jobs), once, for good.

Home Assistant runs shutdown jobs before it stops Bluetooth and the ESPHome proxies. A job must
drop an open link (including one only kept alive by the 30 s idle timer), latch the transport so
nothing reconnects in this process, stay inside its time budget and never raise.

transport.py imports real `homeassistant`/`bleak` at module scope (see test_entity_build.py), so
these tests are skipped where those packages are not installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

HAVE_HA = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "bleak", "bleak_retry_connector")
)

if HAVE_HA:
    from custom_components.blueshark import _async_setup_guided_entry, async_setup  # noqa: F401
    from custom_components.blueshark import coordinator as coordinator_module
    from custom_components.blueshark import transport as transport_module
    from custom_components.blueshark.codecs import get_codec
    from custom_components.blueshark.const import (
        CONF_ADDRESS,
        CONF_CHARACTERISTIC,
        CONF_CODEC_ID,
        DATA_TRANSPORTS,
        DOMAIN,
    )
    from custom_components.blueshark.coordinator import (
        BlueSharkDevice,
        async_get_transport,
        async_release_unowned_transports_at_shutdown,
    )
    from custom_components.blueshark.transport import ShuttingDownError

ADDRESS = "AA:BB:CC:DD:EE:FF"
CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"


class _FakeServices:
    def get_characteristic(self, _uuid):
        return SimpleNamespace(properties=["write-without-response"])

    def __iter__(self):
        return iter(())


class FakeGatt:
    """The subset of a bleak client that the transport touches."""

    def __init__(self) -> None:
        self.connected = True
        self.disconnect_calls = 0
        self.hang_on_disconnect = False
        self.services = _FakeServices()

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def start_notify(self, *_args, **_kwargs) -> None:
        return None

    async def write_gatt_char(self, *_args, **_kwargs) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.hang_on_disconnect:
            await asyncio.sleep(3600)
        self.connected = False


@unittest.skipUnless(HAVE_HA, "needs homeassistant, bleak and bleak-retry-connector")
class ShutdownReleaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.gatt = FakeGatt()
        self.connects = 0
        self.idle_unsub = MagicMock()

        async def _establish(*_args, **_kwargs):
            self.connects += 1
            self.gatt.connected = True
            return self.gatt

        patches = [
            patch.object(transport_module, "establish_connection", side_effect=_establish),
            patch.object(
                transport_module.bluetooth,
                "async_ble_device_from_address",
                return_value=SimpleNamespace(address=ADDRESS, name="dev"),
            ),
            patch.object(transport_module, "async_call_later", return_value=self.idle_unsub),
        ]
        for started in patches:
            started.start()
            self.addCleanup(started.stop)

        self.hass = SimpleNamespace(data={})
        self.transport = async_get_transport(self.hass, ADDRESS, "Fan")

    def _device(self) -> "BlueSharkDevice":
        entry = SimpleNamespace(
            data={CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR},
            options={},
            title="Fan",
        )
        device = BlueSharkDevice(self.hass, entry, self.transport, get_codec("raw", {}))
        self.hass.data[DOMAIN][entry.title] = device
        return device

    # -- registration -----------------------------------------------------------------------

    async def test_guided_entry_registers_one_removable_shutdown_job(self) -> None:
        remove = MagicMock()
        hass = MagicMock()
        hass.data = {}
        hass.async_add_shutdown_job.return_value = remove
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        entry = MagicMock()
        entry.title = "Fan"
        entry.data = {CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR, CONF_CODEC_ID: "raw"}
        entry.options = {}

        with patch.object(BlueSharkDevice, "async_start"):
            self.assertTrue(await _async_setup_guided_entry(hass, entry))

        hass.async_add_shutdown_job.assert_called_once()
        (job,) = hass.async_add_shutdown_job.call_args.args
        self.assertEqual(job.target, hass.data[DOMAIN][entry.entry_id].async_release_at_shutdown)
        entry.async_on_unload.assert_any_call(remove)  # unloading the entry removes the job

    # -- the release ------------------------------------------------------------------------

    async def test_job_releases_an_idle_timer_link_and_latches(self) -> None:
        device = self._device()
        await self.transport.request(CHAR, b"\x01", 10)  # leaves the link up, idle timer armed
        self.assertTrue(self.gatt.connected)
        self.assertTrue(self.transport.connected)

        with self.assertLogs(coordinator_module._LOGGER, "INFO") as logs:
            await device.async_release_at_shutdown()

        self.assertFalse(self.gatt.connected)
        self.assertEqual(self.gatt.disconnect_calls, 1)
        self.assertFalse(self.transport.connected)
        self.idle_unsub.assert_called()
        self.assertIn("Released BLE link to Fan at shutdown", "\n".join(logs.output))

        # Latched: no operation may open the link again, and the radio is never touched.
        with self.assertRaises(ShuttingDownError):
            await self.transport.request(CHAR, b"\x01", 10)
        with self.assertRaises(ShuttingDownError):
            await self.transport.enumerate_gatt()
        with self.assertRaises(ShuttingDownError):
            await self.transport.listen(CHAR, 0.01, lambda *_: None)
        self.assertEqual(self.connects, 1)
        self.assertFalse(self.transport.is_busy())  # a refused operation must not leak the lock

    async def test_connect_in_flight_when_shutdown_begins_is_handed_back(self) -> None:
        started, proceed = asyncio.Event(), asyncio.Event()

        async def _slow_establish(*_args, **_kwargs):
            started.set()
            await proceed.wait()
            self.gatt.connected = True
            return self.gatt

        with patch.object(transport_module, "establish_connection", side_effect=_slow_establish):
            sending = asyncio.create_task(self.transport.request(CHAR, b"\x01", 10))
            await started.wait()
            await self._device().async_release_at_shutdown()  # nothing to drop yet, but latches
            proceed.set()
            with self.assertRaises(ShuttingDownError):
                await sending

        self.assertFalse(self.gatt.connected)
        self.assertFalse(self.transport.connected)

    async def test_request_waiting_for_its_reply_fails_cleanly_when_link_is_dropped(self) -> None:
        sending = asyncio.create_task(self.transport.request(CHAR, b"\x01", 5000))
        await asyncio.sleep(0.05)
        self.assertTrue(self.transport.is_busy())

        await self._device().async_release_at_shutdown()

        raw = await asyncio.wait_for(sending, 1)  # resolves with "no response", not a crash
        self.assertIsNone(raw.response)
        self.assertFalse(self.gatt.connected)

    async def test_hanging_disconnect_is_bounded_and_does_not_raise(self) -> None:
        device = self._device()
        await self.transport.request(CHAR, b"\x01", 10)
        self.gatt.hang_on_disconnect = True

        with (
            patch.object(coordinator_module, "SHUTDOWN_RELEASE_TIMEOUT_S", 0.2),
            self.assertLogs(coordinator_module._LOGGER, "WARNING") as logs,
        ):
            await asyncio.wait_for(device.async_release_at_shutdown(), 2)  # returns, not raises

        self.assertIn("Timed out", "\n".join(logs.output))
        with self.assertRaises(ShuttingDownError):  # latched even though the disconnect hung
            await self.transport.request(CHAR, b"\x01", 10)

    async def test_failing_disconnect_does_not_raise(self) -> None:
        device = self._device()
        await self.transport.request(CHAR, b"\x01", 10)
        self.gatt.disconnect = AsyncMock(side_effect=RuntimeError("proxy went away"))

        with self.assertLogs(coordinator_module._LOGGER, "WARNING") as logs:
            await device.async_release_at_shutdown()

        self.assertIn("Could not release the BLE link", "\n".join(logs.output))

    # -- wizard transports (no config entry) -------------------------------------------------

    async def test_domain_job_releases_only_transports_no_entry_owns(self) -> None:
        owned_device = self._device()  # owns ADDRESS's transport
        wizard = async_get_transport(self.hass, "11:22:33:44:55:66", "Wizard probe")
        await wizard.enumerate_gatt()
        self.assertTrue(wizard.connected)
        owned_device.transport.async_release_for_shutdown = AsyncMock()

        await async_release_unowned_transports_at_shutdown(self.hass)

        self.assertFalse(wizard.connected)
        owned_device.transport.async_release_for_shutdown.assert_not_awaited()
        self.assertEqual(set(self.hass.data[DOMAIN][DATA_TRANSPORTS]), {ADDRESS, "11:22:33:44:55:66"})
        with self.assertRaises(ShuttingDownError):
            await wizard.enumerate_gatt()


if __name__ == "__main__":
    unittest.main()
