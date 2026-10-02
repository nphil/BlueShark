"""Generic BLE transport: one connection, one lock, any characteristic.

This module knows nothing about CoolLED, Govee, or any other protocol - it moves bytes to
and from a GATT characteristic over Home Assistant's shared Bluetooth stack (which may be a
local adapter or, in the common case for this integration, an ESPHome proxy; there is no
guarantee any local adapter exists at all). Protocol framing lives in :mod:`.codecs`.

Design constraints (see the project brief this was built against):

- Exactly one BLE operation in flight per physical device. A second caller while one is
  already running gets :class:`BusyError` immediately - the engine does not queue operations
  behind each other, because a queued write's response could be mistaken for a different
  request's response on a device that only ever answers on one notify characteristic.
- Every connect resolves the freshest connectable ``BLEDevice`` for the address immediately
  before connecting, rather than reusing one resolved once at setup. Reusing a stale
  ``BLEDevice`` (obtained long ago, potentially through a proxy that has since gone away) is
  the most common reason BLE-over-proxy integrations silently stop working after a proxy
  restart or Wi-Fi hiccup.
- ``bleak_retry_connector.establish_connection``'s ``ble_device_callback`` parameter is
  accepted here for forward/backward compatibility, but is a documented no-op as of
  bleak-retry-connector >= 4.x ("Deprecated and unused ... Pass the freshest BLEDevice to
  device instead"). The real fix is exactly that: `device` itself is re-resolved immediately
  before every call to `establish_connection`, so it is already fresh regardless of whether
  the callback is ever invoked.
- The idle-disconnect timer is scheduled with `async_call_later`, a callback registered with
  the event loop - it never holds `_lock` while waiting, so it can never itself deadlock a
  future request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.helpers.event import async_call_later

from . import shutdown

if TYPE_CHECKING:
    import asyncio

    from bleak.backends.device import BLEDevice
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class TransportError(Exception):
    """Base class for every error this module raises."""


class NoRouteError(TransportError):
    """No connectable BLEDevice is currently reachable for this address."""


class BusyError(TransportError):
    """Another operation already holds the device's single connection slot."""

    def __init__(self, address: str, holder: str | None) -> None:
        self.address = address
        self.holder = holder
        super().__init__(f"{address} is busy: {holder or 'another operation'}")


class UnsupportedOperationError(TransportError):
    """The requested characteristic/operation is not something this device supports."""


class TransportDisconnectedError(TransportError):
    """The device disconnected while a request was still awaiting its response."""


class ShuttingDownError(TransportError):
    """Home Assistant is shutting down: this process must not open another BLE connection."""


@dataclass(frozen=True)
class RawResponse:
    """One request/response cycle, before any codec has interpreted it."""

    sent: bytes
    response: bytes | None
    elapsed_ms: int


@dataclass
class GattCharacteristicInfo:
    uuid: str
    handle: int
    properties: list[str]


@dataclass
class GattServiceInfo:
    uuid: str
    characteristics: list[GattCharacteristicInfo]


@dataclass
class _NotifyState:
    """Everything routed through one characteristic's single active subscription."""

    pending: asyncio.Future[bytes] | None = None
    listeners: list[Callable[[float, bytes], None]] = field(default_factory=list)


class BleTransport:
    """The single physical connection to one BLE device, shared by every caller.

    One instance per address, looked up through :mod:`.runtime` so a wizard session (no
    config entry yet) and a fully configured entry never open two competing connections to
    the same device.
    """

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        self._hass = hass
        self.address = address
        self.name = name
        self._lock: asyncio.Lock
        import asyncio as _asyncio

        self._lock = _asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._notify: dict[str, _NotifyState] = {}
        self._idle_disconnect_s = 30
        self._idle_unsub: Callable[[], None] | None = None
        self._current_operation: str | None = None
        self.connected = False
        # Set once by `async_release_for_shutdown` and never cleared (Home Assistant is going down).
        self._closing = False
        self.last_unsolicited: bytes | None = None
        self.on_connection_changed: Callable[[bool], None] | None = None

    @property
    def _is_closing(self) -> bool:
        """This transport was latched by its own release, or the whole integration was (domain latch)."""

        return self._closing or shutdown.in_progress(self._hass)

    # ------------------------------------------------------------------ options

    def set_idle_disconnect_s(self, seconds: int) -> None:
        self._idle_disconnect_s = seconds
        if seconds <= 0:
            self._cancel_idle_timer()

    @property
    def current_operation(self) -> str | None:
        return self._current_operation

    # ------------------------------------------------------------------ the one lock

    def _begin_operation(self, name: str) -> None:
        if self._is_closing:
            raise ShuttingDownError(f"{self.address}: Home Assistant is shutting down; not connecting")
        if self._lock.locked():
            raise BusyError(self.address, self._current_operation)
        self._cancel_idle_timer()

    async def _acquire(self, name: str) -> None:
        self._begin_operation(name)
        await self._lock.acquire()
        self._current_operation = name

    def _release(self) -> None:
        self._current_operation = None
        self._lock.release()
        self._schedule_idle_timer()

    # ------------------------------------------------------------------ connect/disconnect

    async def _ensure_connected(self) -> None:
        if self._is_closing:
            raise ShuttingDownError(f"{self.address}: Home Assistant is shutting down; not connecting")
        if self._client is not None and self._client.is_connected:
            return
        device = bluetooth.async_ble_device_from_address(self._hass, self.address, True)
        if device is None:
            raise NoRouteError(
                f"no connectable BLE route to {self.address}: no proxy or adapter currently "
                "reports it as reachable for a connection"
            )

        def _freshest() -> BLEDevice:
            fresh = bluetooth.async_ble_device_from_address(self._hass, self.address, True)
            return fresh if fresh is not None else device

        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.name,
            disconnected_callback=self._on_disconnected,
            max_attempts=3,
            ble_device_callback=_freshest,
        )
        if self._is_closing:
            # Shutdown began while the connect was in flight: hand the link straight back.
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - best effort while shutting down
                _LOGGER.debug("%s: disconnect raised; connection was already gone", self.address, exc_info=True)
            raise ShuttingDownError(f"{self.address}: Home Assistant is shutting down; not connecting")
        self._client = client
        self._notify.clear()
        self._set_connected(True)

    def _require_client(self) -> None:
        """Raise if the link was dropped (an unload or the shutdown release) since the connect."""

        if self._client is None:
            raise TransportDisconnectedError("device disconnected")

    def _set_connected(self, connected: bool) -> None:
        self.connected = connected
        if self.on_connection_changed is not None:
            self.on_connection_changed(connected)

    def _on_disconnected(self, _client: object) -> None:
        # Bleak calls this synchronously (not a coroutine); it must not block.
        self._set_connected(False)
        for state in self._notify.values():
            future = state.pending
            if future is not None and not future.done():
                future.set_exception(TransportDisconnectedError("device disconnected"))
        self._notify.clear()

    async def _disconnect_locked(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        self._notify.clear()
        self._set_connected(False)
        try:
            await client.disconnect()
        except BleakError:
            _LOGGER.debug("%s: disconnect raised; connection was already gone", self.address, exc_info=True)

    async def async_disconnect(self) -> None:
        """Disconnect, waiting for any in-flight operation rather than answering busy.

        Used on unload/idle-timeout, where "actually disconnect" is the point rather than
        one of several competing operations.
        """

        self._cancel_idle_timer()
        async with self._lock:
            await self._disconnect_locked()

    async def async_release_for_shutdown(self) -> None:
        """Home Assistant is stopping: latch the transport closed, then drop any open link.

        The latch comes first, so no caller (command, wizard probe, idle reconnect) can open a
        new connection afterwards. The link is dropped WITHOUT waiting for the lock, so a long
        ``listen`` or a request awaiting its reply cannot hold the release hostage; a request
        then ends without a reply. A connect still in
        flight notices the latch when it completes and hands its link back. One-way, unlike
        :meth:`async_disconnect`. Callers bound it with a timeout.
        """

        self._closing = True
        self._cancel_idle_timer()
        # Wake a request that is still waiting for its reply instead of letting it sit out its timeout.
        for state in self._notify.values():
            if state.pending is not None and not state.pending.done():
                state.pending.set_exception(TransportDisconnectedError("shutting down"))
        await self._disconnect_locked()

    # ------------------------------------------------------------------ idle timer

    def _cancel_idle_timer(self) -> None:
        if self._idle_unsub is not None:
            self._idle_unsub()
            self._idle_unsub = None

    def _schedule_idle_timer(self) -> None:
        self._cancel_idle_timer()
        if self._idle_disconnect_s <= 0 or self._client is None or self._is_closing:
            return
        self._idle_unsub = async_call_later(self._hass, self._idle_disconnect_s, self._on_idle_timeout)

    async def _on_idle_timeout(self, _now: object) -> None:
        self._idle_unsub = None
        if self._lock.locked():
            # An operation started in the same tick the timer fired; it will reschedule the
            # idle timer itself when it finishes. Nothing to do here.
            return
        async with self._lock:
            await self._disconnect_locked()

    # ------------------------------------------------------------------ notifications

    def _notify_state(self, characteristic: str) -> _NotifyState:
        state = self._notify.get(characteristic)
        if state is None:
            state = _NotifyState()
            self._notify[characteristic] = state
        return state

    async def _ensure_notify(self, characteristic: str) -> None:
        if characteristic in self._notify:
            return
        state = self._notify_state(characteristic)

        def _handler(_sender: object, data: bytearray) -> None:
            payload = bytes(data)
            if state.pending is not None and not state.pending.done():
                state.pending.set_result(payload)
                return
            self.last_unsolicited = payload
            _LOGGER.debug("%s: unsolicited notification on %s: %s", self.address, characteristic, payload.hex())
            for listener in list(state.listeners):
                listener(time.monotonic(), payload)

        self._require_client()
        await self._client.start_notify(characteristic, _handler)

    def _write_kwargs(self, characteristic: str) -> bool:
        """Return whether to request a confirmed write (True) or write-without-response."""

        self._require_client()
        char = self._client.services.get_characteristic(characteristic)
        if char is None:
            raise UnsupportedOperationError(f"characteristic {characteristic} is not exposed by this device")
        properties = {str(prop).lower() for prop in char.properties}
        return "write-without-response" not in properties

    # ------------------------------------------------------------------ public operations

    async def request(self, characteristic: str, payload: bytes, await_response_ms: int) -> RawResponse:
        """Write `payload` to `characteristic`, then wait for its next notification."""

        await self._acquire(f"send {characteristic}")
        try:
            await self._ensure_connected()
            await self._ensure_notify(characteristic)
            state = self._notify_state(characteristic)
            import asyncio as _asyncio

            loop = _asyncio.get_running_loop()
            future: asyncio.Future[bytes] = loop.create_future()
            state.pending = future
            start = time.monotonic()
            try:
                self._require_client()
                await self._client.write_gatt_char(
                    characteristic, payload, response=self._write_kwargs(characteristic)
                )
                try:
                    response = await _asyncio.wait_for(future, timeout=await_response_ms / 1000)
                except _asyncio.TimeoutError:
                    response = None
                except TransportDisconnectedError:
                    response = None
            finally:
                if state.pending is future:
                    state.pending = None
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return RawResponse(sent=payload, response=response, elapsed_ms=elapsed_ms)
        finally:
            self._release()

    async def enumerate_gatt(self) -> list[GattServiceInfo]:
        """Connect and read back every service/characteristic the device exposes."""

        await self._acquire("enumerate")
        try:
            await self._ensure_connected()
            self._require_client()
            services: list[GattServiceInfo] = []
            for service in self._client.services:
                services.append(
                    GattServiceInfo(
                        uuid=service.uuid,
                        characteristics=[
                            GattCharacteristicInfo(
                                uuid=char.uuid,
                                handle=char.handle,
                                properties=list(char.properties),
                            )
                            for char in service.characteristics
                        ],
                    )
                )
            return services
        finally:
            self._release()

    async def listen(
        self, characteristic: str, seconds: float, on_frame: Callable[[float, bytes], None]
    ) -> None:
        """Subscribe to `characteristic` for `seconds`, delivering every frame with a timestamp."""

        await self._acquire(f"listen {characteristic}")
        try:
            await self._ensure_connected()
            await self._ensure_notify(characteristic)
            state = self._notify_state(characteristic)
            state.listeners.append(on_frame)
            start = time.monotonic()

            def _timestamped(_at: float, payload: bytes) -> None:
                on_frame(time.monotonic() - start, payload)

            # Route this listener's own timestamp base rather than the shared handler's.
            state.listeners[-1] = _timestamped
            import asyncio as _asyncio

            try:
                await _asyncio.sleep(seconds)
            finally:
                if _timestamped in state.listeners:
                    state.listeners.remove(_timestamped)
        finally:
            self._release()

    def is_busy(self) -> bool:
        return self._lock.locked()
