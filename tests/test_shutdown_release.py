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
    from custom_components.blueshark import shutdown

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


@unittest.skipUnless(HAVE_HA, "needs homeassistant, bleak and bleak-retry-connector")
class DomainLatchTests(unittest.IsolatedAsyncioTestCase):
    """Rules A-D of the addendum: Home Assistant reads its job list once, at the start of Stage 1."""

    def setUp(self) -> None:
        self.gatt = FakeGatt()
        self.connects = 0

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
            patch.object(transport_module, "async_call_later", return_value=MagicMock()),
        ]
        for started in patches:
            started.start()
            self.addCleanup(started.stop)

        self.hass = SimpleNamespace(data={})

    def _guided_hass(self) -> MagicMock:
        hass = MagicMock()
        hass.data = {}
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        return hass

    def _guided_entry(self) -> MagicMock:
        entry = MagicMock()
        entry.title = "Fan"
        entry.data = {CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR, CONF_CODEC_ID: "raw"}
        entry.options = {}
        return entry

    # -- A: the domain job -------------------------------------------------------------------

    async def test_domain_job_is_registered_by_async_setup_before_its_first_await(self) -> None:
        hass = self._guided_hass()
        seen_at_first_await: list[int] = []

        async def _register_static_paths(_paths) -> None:
            seen_at_first_await.append(hass.async_add_shutdown_job.call_count)

        hass.http.async_register_static_paths = _register_static_paths
        with (
            patch("custom_components.blueshark.websocket_api.async_register_commands"),
            patch("custom_components.blueshark.services.async_register_services"),
            patch("homeassistant.components.frontend.async_register_built_in_panel"),
        ):
            self.assertTrue(await async_setup(hass, {}))

        self.assertEqual(seen_at_first_await, [1])
        hass.async_add_shutdown_job.assert_called_once()
        job = hass.async_add_shutdown_job.call_args.args[0]
        self.assertEqual(job.target, async_release_unowned_transports_at_shutdown)

    async def test_domain_job_latches_every_transport_even_ones_an_entry_owns(self) -> None:
        owned = async_get_transport(self.hass, ADDRESS, "Fan")
        entry = SimpleNamespace(data={CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR}, options={}, title="Fan")
        self.hass.data[DOMAIN]["entry-id"] = BlueSharkDevice(self.hass, entry, owned, get_codec("raw", {}))
        await owned.request(CHAR, b"\x01", 10)
        self.assertTrue(self.gatt.connected)

        await async_release_unowned_transports_at_shutdown(self.hass)

        self.assertTrue(shutdown.in_progress(self.hass))
        # The domain job leaves the owned link to its entry's own job, but nothing may reopen it.
        with self.assertRaises(ShuttingDownError):
            await owned.request(CHAR, b"\x01", 10)
        self.assertEqual(self.connects, 1)

    async def test_transport_created_after_the_latch_refuses_to_connect(self) -> None:
        """A wizard probe arriving mid-Stage-1 creates a fresh transport nobody released."""
        shutdown.begin(self.hass)
        fresh = async_get_transport(self.hass, "11:22:33:44:55:66", "Late wizard probe")

        with self.assertRaises(ShuttingDownError):
            await fresh.enumerate_gatt()
        self.assertEqual(self.connects, 0)
        self.assertFalse(fresh.is_busy())

    # -- B: setup refuses --------------------------------------------------------------------

    async def test_guided_setup_and_reload_refuse_while_latched(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        hass = self._guided_hass()
        shutdown.begin(hass)

        with patch.object(BlueSharkDevice, "async_start") as start:
            with self.assertRaises(ConfigEntryNotReady):
                await _async_setup_guided_entry(hass, self._guided_entry())

        start.assert_not_called()  # no watchers started
        hass.async_add_shutdown_job.assert_not_called()
        hass.config_entries.async_forward_entry_setups.assert_not_awaited()
        self.assertNotIn(DATA_TRANSPORTS, hass.data.get(DOMAIN, {}))  # not even a transport was created

    # -- C: the per-entry job exists before setup first yields ---------------------------------

    async def test_entry_job_is_registered_before_setup_first_yields(self) -> None:
        hass = self._guided_hass()
        seen: list[int] = []

        async def _forward(*_args) -> None:
            seen.append(hass.async_add_shutdown_job.call_count)

        hass.config_entries.async_forward_entry_setups = _forward

        with patch.object(BlueSharkDevice, "async_start"):
            self.assertTrue(await _async_setup_guided_entry(hass, self._guided_entry()))

        self.assertEqual(seen, [1])

    # -- D: refusals are not faults ----------------------------------------------------------

    async def test_refused_command_leaves_no_trace_of_a_failure(self) -> None:
        from custom_components.blueshark.const import CONF_COMMAND_MAP

        transport = async_get_transport(self.hass, ADDRESS, "Fan")
        entry = SimpleNamespace(
            data={CONF_ADDRESS: ADDRESS, CONF_CHARACTERISTIC: CHAR},
            options={CONF_COMMAND_MAP: {"power": {"kind": "button", "payload_hex": "0801"}}},
            title="Fan",
        )
        device = BlueSharkDevice(self.hass, entry, transport, get_codec("raw", {}))
        notified = MagicMock()
        device.async_add_listener(notified)
        shutdown.begin(self.hass)

        with self.assertRaises(ShuttingDownError):
            await device.async_send_command("power")

        self.assertIsNone(device.last_response)
        self.assertEqual(device.opcode_log, [])
        notified.assert_not_called()
        self.assertEqual(self.connects, 0)


if __name__ == "__main__":
    unittest.main()
