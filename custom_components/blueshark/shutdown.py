"""The process-lifetime "Home Assistant is shutting down" latch.

Home Assistant runs its shutdown jobs (``hass.async_add_shutdown_job``) before it fires
``EVENT_HOMEASSISTANT_STOP``, and ``hass.state`` is still ``running`` while they run, so
``hass.is_stopping`` cannot tell. It also reads its job list once, when the stage starts: a job
registered later (by an entry that is set up or reloaded during the stage) is never run. So the
latch lives in ``hass.data[DOMAIN]``, is set by a domain-level job registered in ``async_setup``
(never removed when an entry unloads), and is checked by every transport and by entry setup.
Once set it is never cleared: nothing in this integration connects again in this process.

Only ``homeassistant.core`` types are imported lazily so the module stays importable without
Home Assistant (the repo's pure tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_KEY = "shutting_down"


def begin(hass: HomeAssistant) -> None:
    """Latch: Home Assistant is shutting down."""

    hass.data.setdefault(DOMAIN, {})[_KEY] = True


def in_progress(hass: HomeAssistant) -> bool:
    """Whether Home Assistant is shutting down (the latch is set)."""

    return bool(hass.data.get(DOMAIN, {}).get(_KEY))


# --- links no transport owns -------------------------------------------------------------------
#
# The legacy profile-import buttons (button.py) connect, write once and disconnect inside one
# awaited press, with no transport object. While a press is in flight its client is registered
# here so the domain shutdown job can find and drop it; it is removed again when the press ends.

_CLIENTS_KEY = "legacy_clients"


def track_client(hass: HomeAssistant, client: object) -> None:
    """Register a connected client that only a button press owns, so shutdown can release it."""

    hass.data.setdefault(DOMAIN, {}).setdefault(_CLIENTS_KEY, set()).add(client)


def untrack_client(hass: HomeAssistant, client: object) -> None:
    """Forget `client` (its press ended)."""

    hass.data.get(DOMAIN, {}).get(_CLIENTS_KEY, set()).discard(client)


def tracked_clients(hass: HomeAssistant) -> list[object]:
    """A snapshot of the clients registered by `track_client`."""

    return list(hass.data.get(DOMAIN, {}).get(_CLIENTS_KEY, ()))
