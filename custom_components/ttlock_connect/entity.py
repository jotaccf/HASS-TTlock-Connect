"""Base entity class for TTLock integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import NOT_CONNECTABLE_REASON, LockUpdateCoordinator


class BaseLockEntity(CoordinatorEntity[LockUpdateCoordinator], ABC):
    """Abstract base class for lock entity."""

    coordinator: LockUpdateCoordinator

    def __init__(
        self,
        coordinator: LockUpdateCoordinator,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = (
            f"{coordinator.unique_id}-{self.__class__.__name__.lower()}"
        )
        self._update_from_coordinator()

        # self.entity_description = description

    @abstractmethod
    def _update_from_coordinator(self) -> None:
        pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_coordinator()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable if non-connectable, or periodic re-verification is failing."""
        return self.coordinator.connectable and super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Explain unavailability, or pass through what the subclass set.

        Falling back to _attr_extra_state_attributes matters: this property
        would otherwise shadow attributes subclasses assign in
        _update_from_coordinator (e.g. the credential sensors' lists).
        """
        if not self.coordinator.connectable:
            return {"unavailable_reason": NOT_CONNECTABLE_REASON}
        return getattr(self, "_attr_extra_state_attributes", None)
