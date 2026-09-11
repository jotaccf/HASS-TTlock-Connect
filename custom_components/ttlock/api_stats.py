"""Track how many calls the integration makes to the TTLock cloud API.

TTLock's developer plans have a monthly API-call budget, and multi-lock
accounts can burn through it without any visibility from HA's side (#320).
Every request TTLockApi makes (api.py) is recorded here, keyed by day and
endpoint, and surfaced as sensors (sensor.py) so usage - and a projection of
the monthly total - is visible on a dashboard instead of requiring debug-log
archaeology.

Counts are persisted via `homeassistant.helpers.storage.Store` (debounced,
not written per call) and shared across every loaded config entry the same
way LockTrafficCapture is (see __init__.py) - TTLock's quota applies to the
developer application, not to each HA config entry, so a single global
tally is the number that matters.

OAuth token requests (oauth2/token) don't pass through TTLockApi.get/post
and are not counted; only /v3/ API calls are.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_API_CALL

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_api_stats"

# How long to keep per-day counts. Two full months so the current month is
# always complete for the monthly total, with a little slack.
RETENTION_DAYS = 62

# Debounce for persisting counts to disk - a poll burst across many locks
# becomes one write, and losing the last few seconds of tally on a hard
# crash is inconsequential for a usage estimate.
SAVE_DELAY_SECONDS = 30


class ApiCallCounter:
    """Persisted per-day, per-endpoint tally of TTLock cloud API calls."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the counter; call async_load before recording."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._days: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load persisted counts from disk."""
        data = await self._store.async_load() or {}
        self._days = data.get("days", {})

    @callback
    def record(self, endpoint: str) -> None:
        """Count one API call to `endpoint`, now.

        Called for every request as it is sent, before knowing whether it
        succeeds - failed requests spend quota all the same.
        """
        today = dt_util.now().date()
        day = self._days.setdefault(today.isoformat(), {"total": 0, "endpoints": {}})
        day["total"] += 1
        day["endpoints"][endpoint] = day["endpoints"].get(endpoint, 0) + 1

        self._prune(today)
        self._store.async_delay_save(lambda: {"days": self._days}, SAVE_DELAY_SECONDS)
        async_dispatcher_send(self._hass, SIGNAL_API_CALL)

    async def async_flush(self) -> None:
        """Persist the tally immediately, bypassing the debounced save."""
        await self._store.async_save({"days": self._days})

    def _prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
        for key in [key for key in self._days if key < cutoff]:
            del self._days[key]

    @property
    def today_count(self) -> int:
        """Calls made so far today."""
        today = dt_util.now().date().isoformat()
        return self._days.get(today, {}).get("total", 0)

    def today_by_endpoint(self) -> dict[str, int]:
        """Today's calls broken down by endpoint, busiest first."""
        today = dt_util.now().date().isoformat()
        endpoints = self._days.get(today, {}).get("endpoints", {})
        return dict(sorted(endpoints.items(), key=lambda item: -item[1]))

    @property
    def month_count(self) -> int:
        """Calls made so far this calendar month."""
        prefix = dt_util.now().date().isoformat()[:8]  # "YYYY-MM-"
        return sum(
            day["total"] for key, day in self._days.items() if key.startswith(prefix)
        )

    def month_by_endpoint(self) -> dict[str, int]:
        """This month's calls broken down by endpoint, busiest first."""
        prefix = dt_util.now().date().isoformat()[:8]
        totals: dict[str, int] = {}
        for key, day in self._days.items():
            if not key.startswith(prefix):
                continue
            for endpoint, count in day.get("endpoints", {}).items():
                totals[endpoint] = totals.get(endpoint, 0) + count
        return dict(sorted(totals.items(), key=lambda item: -item[1]))

    @property
    def projected_month_count(self) -> int:
        """The month's total if calls continue at the month-to-date rate."""
        now = dt_util.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        elapsed_days = (now.day - 1) + (now.hour * 3600 + now.minute * 60) / 86400
        # Too early in the month for the rate to mean anything - report the
        # tally itself rather than a wild extrapolation.
        if elapsed_days < 0.25:
            return self.month_count
        return round(self.month_count * days_in_month / elapsed_days)
