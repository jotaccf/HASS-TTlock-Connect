"""Support for iCloud sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_stats import ApiCallCounter
from .ble import async_bluetooth_available
from .const import CONF_WEBHOOK_STATUS, DOMAIN, SIGNAL_API_CALL, TT_COUNTER
from .coordinator import (
    LockUpdateCoordinator,
    async_add_when_sensor_present,
    lock_coordinators,
)
from .entity import BaseLockEntity
from .models import EkeyStatus, Features
from .usage_estimate import estimate_monthly_calls

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all the locks for the config entry."""

    coordinators = list(lock_coordinators(hass, entry))

    with_bluetooth = async_bluetooth_available(hass)

    counter: ApiCallCounter = hass.data[DOMAIN][entry.entry_id][TT_COUNTER]

    async_add_entities(
        [
            *(
                entity
                for coordinator in coordinators
                for entity in (
                    LockBattery(coordinator),
                    LockOperator(coordinator),
                    LockTrigger(coordinator),
                    LockPinCodes(coordinator),
                    LockEkeys(coordinator),
                    *([LockGateway(coordinator)] if coordinator.has_gateway else []),
                    *([LockBleSignal(coordinator)] if with_bluetooth else []),
                )
            ),
            ApiCallsToday(entry, counter),
            ApiCallsThisMonth(entry, counter),
            ApiCallsEstimatedMonthly(entry, coordinators),
        ]
    )

    for lock_coordinator in coordinators:
        async_add_when_sensor_present(
            lock_coordinator,
            lambda lock_coordinator=lock_coordinator: async_add_entities(
                [SensorBattery(lock_coordinator)]
            ),
        )


class LockBattery(BaseLockEntity, SensorEntity):
    """Representation of a locks battery state."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Battery"
        self._attr_native_value = self.coordinator.data.battery_level


class LockOperator(BaseLockEntity, RestoreEntity, SensorEntity):
    """Representation of a locks last operator."""

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Last Operator"
        if self.coordinator.data.last_user:
            self._attr_native_value = self.coordinator.data.last_user
        elif not self._attr_native_value:
            self._attr_native_value = "Unknown"

    async def async_added_to_hass(self) -> None:
        """Restore on startup since we don't have event history."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if not last_state or last_state.state == STATE_UNAVAILABLE:
            return

        self._attr_native_value = last_state.state


class LockTrigger(BaseLockEntity, RestoreEntity, SensorEntity):
    """Representation of a locks state change reason."""

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Last Trigger"
        if self.coordinator.data.last_reason:
            self._attr_native_value = self.coordinator.data.last_reason
        elif not self._attr_native_value:
            self._attr_native_value = "Unknown"

    async def async_added_to_hass(self) -> None:
        """Restore on startup since we don't have event history."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if not last_state or last_state.state == STATE_UNAVAILABLE:
            return

        self._attr_native_value = last_state.state


class SensorBattery(BaseLockEntity, SensorEntity):
    """Representation of sensor battery."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Sensor Battery"
        self._attr_native_value = (
            self.coordinator.data.sensor.battery
            if self.coordinator.data.sensor
            else None
        )


class LockPinCodes(BaseLockEntity, SensorEntity):
    """The lock's PIN codes, mirroring the TTLock app's passcode list.

    State is the number of currently valid (non-expired) codes; the full
    list - names, codes and validity windows - is in the attributes, so a
    markdown or entities card can show it like the app does. Refreshed on
    the slow poll tier and immediately after any passcode action.
    """

    _attr_icon = "mdi:dialpad"

    def _update_from_coordinator(self) -> None:
        """Count the valid codes and expose the full list as attributes."""
        self._attr_name = f"{self.coordinator.data.name} PIN Codes"
        passcodes = self.coordinator.data.passcodes
        if passcodes is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}
            return
        self._attr_native_value = sum(1 for code in passcodes if not code.expired)
        self._attr_extra_state_attributes = {
            "total": len(passcodes),
            "pin_codes": [
                {
                    "id": code.id,
                    "name": code.name,
                    "passcode": code.passcode,
                    "type": code.type.name if code.type is not None else None,
                    "start_date": code.start_date.isoformat()
                    if code.start_date
                    else None,
                    "end_date": code.end_date.isoformat() if code.end_date else None,
                    "expired": code.expired,
                }
                for code in passcodes
            ],
        }


class LockEkeys(BaseLockEntity, SensorEntity):
    """The lock's eKeys, mirroring the TTLock app's eKey list.

    State is the number of usable (non-expired, non-frozen) keys; the full
    list - receiver, status, validity, remote-unlock right - is in the
    attributes. Refreshed on the slow poll tier and immediately after any
    eKey action.
    """

    _attr_icon = "mdi:key-wireless"

    def _update_from_coordinator(self) -> None:
        """Count the usable keys and expose the full list as attributes."""
        self._attr_name = f"{self.coordinator.data.name} eKeys"
        ekeys = self.coordinator.data.ekeys
        if ekeys is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}
            return
        self._attr_native_value = sum(
            1
            for ekey in ekeys
            if not ekey.expired and ekey.status is not EkeyStatus.frozen
        )
        self._attr_extra_state_attributes = {
            "total": len(ekeys),
            "ekeys": [
                {
                    "id": ekey.id,
                    "name": ekey.name,
                    "username": ekey.username,
                    "status": ekey.status.name,
                    "start_date": ekey.start_date.isoformat()
                    if ekey.start_date
                    else None,
                    "end_date": ekey.end_date.isoformat() if ekey.end_date else None,
                    "expired": ekey.expired,
                    "remote_enable": bool(ekey.remote_enable),
                }
                for ekey in ekeys
            ],
        }


class LockGateway(BaseLockEntity, SensorEntity):
    """RSSI of the gateway currently used to reach the lock.

    Diagnostic and disabled by default - the same connectivity is already
    surfaced via device_info.via_device (coordinator.py); this is for
    troubleshooting placement/coverage, not everyday use.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Gateway Signal"
        best = self.coordinator.data.best_gateway
        self._attr_native_value = best.rssi if best else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Name/mac of the connected gateway, plus any others in range."""
        gateways = self.coordinator.data.gateways
        if not gateways:
            return None
        best, *others = gateways
        return {
            "gateway": best.name,
            "mac": best.mac,
            "other_gateways": [
                {"name": gateway.name, "mac": gateway.mac, "rssi": gateway.rssi}
                for gateway in others
            ],
        }


class LockBleSignal(BaseLockEntity, SensorEntity):
    """RSSI of the lock as heard directly by HA's own Bluetooth stack.

    Distinct from LockGateway, which reports how well the *TTLock gateway*
    hears the lock - this reports how well *we* hear it, with no TTLock
    hardware in between. Diagnostic and disabled by default: it exists to
    answer "is this lock within local radio range, and would a Bluetooth
    proxy help?", which is a placement question, not a dashboard one.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_from_coordinator(self) -> None:
        """Fetch state from the device."""
        self._attr_name = f"{self.coordinator.data.name} Bluetooth Signal"
        self._attr_native_value = self.coordinator.ble.rssi

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Which adapter/proxy heard the lock, when, and whether it can connect."""
        ble = self.coordinator.ble
        if not ble.in_range:
            return None
        return {
            "source": ble.source,
            "connectable": ble.connectable,
            "last_seen": ble.last_seen.isoformat() if ble.last_seen else None,
        }


class ApiUsageSensor(SensorEntity):
    """Base for the cloud-API usage sensors backed by ApiCallCounter.

    Not a lock entity - usage is account-wide, so these live on their own
    service device ("TTLock Cloud API") rather than under any lock. They
    update on the SIGNAL_API_CALL dispatcher signal fired for every recorded
    request, so the tally on the dashboard is live. The counter itself is
    shared install-wide (see __init__.py); with several config entries each
    entry gets its own sensor pair, all reporting the same shared numbers -
    TTLock's quota is per developer application, so that's the number that
    matters either way.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "calls"
    _attr_icon = "mdi:api"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, counter: ApiCallCounter) -> None:
        """Initialize with the entry (for unique ids) and the shared counter."""
        self._counter = counter
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"api-usage-{entry.entry_id}")},
            name="TTLock Cloud API",
            manufacturer="TT Lock",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the per-call signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_API_CALL, self._on_api_call)
        )

    @callback
    def _on_api_call(self) -> None:
        self.async_write_ha_state()


class ApiCallsToday(ApiUsageSensor):
    """Cloud API calls made so far today."""

    def __init__(self, entry: ConfigEntry, counter: ApiCallCounter) -> None:
        """Set up the daily tally sensor."""
        super().__init__(entry, counter)
        self._attr_unique_id = f"{entry.entry_id}-api-calls-today"
        self._attr_name = "TTLock API Calls Today"

    @property
    def native_value(self) -> int:
        """Today's tally."""
        return self._counter.today_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Today's calls by endpoint, busiest first."""
        return {"by_endpoint": self._counter.today_by_endpoint()}


class ApiCallsEstimatedMonthly(SensorEntity):
    """What the configured polling cadence costs per month, by the math.

    Unlike ApiCallsThisMonth (measured) this is computed from the options
    and device counts via usage_estimate.py - it's the number the options
    flow promises, kept visible on the dashboard. Static per config: options
    changes reload the entry, which rebuilds this entity with fresh values.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "calls"
    _attr_icon = "mdi:calculator"
    _attr_should_poll = False

    def __init__(
        self, entry: ConfigEntry, coordinators: list[LockUpdateCoordinator]
    ) -> None:
        """Compute the estimate for this entry's options and locks."""
        self._attr_unique_id = f"{entry.entry_id}-api-calls-estimate"
        self._attr_name = "TTLock API Calls Estimated Monthly"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"api-usage-{entry.entry_id}")},
            name="TTLock Cloud API",
            manufacturer="TT Lock",
            entry_type=DeviceEntryType.SERVICE,
        )

        connectable = [
            coordinator for coordinator in coordinators if coordinator.connectable
        ]
        self._estimate = estimate_monthly_calls(
            entry.options,
            connectable_locks=len(connectable),
            locks_with_gateway=sum(
                1 for coordinator in connectable if coordinator.has_gateway
            ),
            locks_with_door_sensor=sum(
                1
                for coordinator in connectable
                if Features.door_sensor in coordinator.data.features
            ),
            webhook_confirmed=bool(entry.data.get(CONF_WEBHOOK_STATUS)),
        )
        self._attr_native_value = self._estimate["total"]
        self._attr_extra_state_attributes = {
            "by_source": {
                key: value for key, value in self._estimate.items() if key != "total"
            },
            "connectable_locks": len(connectable),
        }


class ApiCallsThisMonth(ApiUsageSensor):
    """Cloud API calls made so far this calendar month, with a projection."""

    def __init__(self, entry: ConfigEntry, counter: ApiCallCounter) -> None:
        """Set up the monthly tally sensor."""
        super().__init__(entry, counter)
        self._attr_unique_id = f"{entry.entry_id}-api-calls-month"
        self._attr_name = "TTLock API Calls This Month"

    @property
    def native_value(self) -> int:
        """This month's tally."""
        return self._counter.month_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Projection at the current rate, plus per-endpoint breakdown."""
        return {
            "projected_month_total": self._counter.projected_month_count,
            "by_endpoint": self._counter.month_by_endpoint(),
        }
