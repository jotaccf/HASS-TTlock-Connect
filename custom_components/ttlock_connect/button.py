"""Buttons to sync TTLock data from the cloud on demand.

The companion to manual-sync mode (const.CONF_MANUAL_SYNC): with scheduled
polling disabled, this is how a user refreshes data. Two flavors:

- SyncNowButton, on the "TTLock Cloud API" service device: one press
  re-runs every lock coordinator's refresh plus the gateway list.
- LockSyncButton, one per lock on that lock's own device: refreshes just
  that lock, so per-lock sync can be laid out lock-by-lock on a dashboard.

Both are also useful with polling enabled, as "refresh now" shortcuts.
"""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import gateway_coordinator, lock_coordinators
from .entity import BaseLockEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sync buttons for the config entry."""
    async_add_entities(
        [
            SyncNowButton(hass, entry),
            *(
                LockSyncButton(coordinator)
                for coordinator in lock_coordinators(hass, entry)
            ),
        ]
    )


class SyncNowButton(ButtonEntity):
    """Fetch fresh lock and gateway data from the TTLock cloud, once."""

    _attr_icon = "mdi:cloud-sync"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up the button on the Cloud API service device."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-sync-now"
        self._attr_name = "TTLock Sync Now"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"api-usage-{entry.entry_id}")},
            name="TTLock Cloud API",
            manufacturer="TT Lock",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        """Refresh every lock coordinator and the gateway list."""
        coordinators = list(lock_coordinators(self.hass, self._entry))
        await asyncio.gather(
            *(coordinator.async_refresh() for coordinator in coordinators),
            gateway_coordinator(self.hass, self._entry).async_refresh(),
        )


class LockSyncButton(BaseLockEntity, ButtonEntity):
    """Fetch fresh data from the TTLock cloud for this one lock."""

    _attr_icon = "mdi:sync"

    def _update_from_coordinator(self) -> None:
        """Track the lock's (renamable) name."""
        self._attr_name = f"{self.coordinator.data.name} Sync"

    async def async_press(self) -> None:
        """Refresh just this lock's coordinator."""
        await self.coordinator.async_refresh()
