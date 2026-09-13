"""Estimate the monthly TTLock cloud API usage of the configured cadence.

Pure arithmetic over the polling options and the account's device counts -
no I/O. Used by the options flow (config_flow.py) to show what the settings
being edited actually cost, and by the "Estimated Monthly" sensor
(sensor.py) so the projection is visible on a dashboard next to the real
tallies from api_stats.py.

The estimate models only scheduled polling (coordinator.py). It deliberately
ignores startup fills, manual actions, lock/unlock commands and BLE-served
polls - those depend on usage, not configuration - so the real count can be
lower (locks in Bluetooth range) or higher (lots of manual actions).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_GATEWAY_POLL_INTERVAL,
    CONF_MANUAL_SYNC,
    CONF_POLL_INTERVAL,
    CONF_SLOW_POLL_INTERVAL,
    CONF_WEBHOOK_ONLY,
    DEFAULT_GATEWAY_POLL_INTERVAL_MINUTES,
    DEFAULT_MANUAL_SYNC,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_SLOW_POLL_INTERVAL_HOURS,
    DEFAULT_WEBHOOK_ONLY,
)

# Average Gregorian month, so the estimate matches a "per month" quota.
DAYS_PER_MONTH = 30.44


def estimate_monthly_calls(
    options: Mapping[str, Any],
    *,
    connectable_locks: int,
    locks_with_gateway: int,
    locks_with_door_sensor: int = 0,
    webhook_confirmed: bool = False,
) -> dict[str, int]:
    """Scheduled cloud calls per month for these options and device counts.

    Returns a breakdown plus "total". All numbers are rounded to whole calls.
    """
    if options.get(CONF_MANUAL_SYNC, DEFAULT_MANUAL_SYNC):
        return {
            "lock_state": 0,
            "lock_detail": 0,
            "gateway_status": 0,
            "door_sensor": 0,
            "total": 0,
        }

    poll_minutes = options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MINUTES)
    slow_hours = options.get(CONF_SLOW_POLL_INTERVAL, DEFAULT_SLOW_POLL_INTERVAL_HOURS)
    gateway_minutes = options.get(
        CONF_GATEWAY_POLL_INTERVAL, DEFAULT_GATEWAY_POLL_INTERVAL_MINUTES
    )
    webhook_only = options.get(CONF_WEBHOOK_ONLY, DEFAULT_WEBHOOK_ONLY)

    polls_per_month = (24 * 60 / poll_minutes) * DAYS_PER_MONTH
    slow_fetches_per_month = (24 / slow_hours) * DAYS_PER_MONTH

    # Fast tier: lock/queryOpenState once per poll per connectable lock -
    # skipped entirely in webhook-only mode once the webhook is confirmed
    # live (see coordinator._webhook_only_active).
    if webhook_only and webhook_confirmed:
        lock_state = 0.0
    else:
        lock_state = connectable_locks * polls_per_month

    # Slow tier: lock/detail + getPassageModeConfig per lock, plus
    # gateway/listByLock for locks reached via a gateway.
    lock_detail = (connectable_locks * 2 + locks_with_gateway) * slow_fetches_per_month

    # gateway/list: one account-wide call per cycle.
    gateway_status = (24 * 60 / gateway_minutes) * DAYS_PER_MONTH

    # doorSensor/query: at most once per day per lock with a sensor.
    door_sensor = locks_with_door_sensor * DAYS_PER_MONTH

    breakdown = {
        "lock_state": round(lock_state),
        "lock_detail": round(lock_detail),
        "gateway_status": round(gateway_status),
        "door_sensor": round(door_sensor),
    }
    return {**breakdown, "total": sum(breakdown.values())}
