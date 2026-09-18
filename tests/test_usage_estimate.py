"""Test the monthly API-usage estimator."""

from custom_components.ttlock_connect.const import (
    CONF_GATEWAY_POLL_INTERVAL,
    CONF_MANUAL_SYNC,
    CONF_POLL_INTERVAL,
    CONF_SLOW_POLL_INTERVAL,
    CONF_WEBHOOK_ONLY,
)
from custom_components.ttlock_connect.usage_estimate import (
    DAYS_PER_MONTH,
    estimate_monthly_calls,
)


def test_defaults_single_lock_with_gateway():
    """Default cadence: 30 min fast, 6 h slow, 15 min gateway."""
    estimate = estimate_monthly_calls(
        {}, connectable_locks=1, locks_with_gateway=1, locks_with_door_sensor=0
    )

    assert estimate["lock_state"] == round(48 * DAYS_PER_MONTH)
    assert estimate["lock_detail"] == round(3 * 4 * DAYS_PER_MONTH)
    assert estimate["credentials"] == round(2 * 4 * DAYS_PER_MONTH)
    assert estimate["gateway_status"] == round(96 * DAYS_PER_MONTH)
    assert estimate["door_sensor"] == 0
    assert estimate["total"] == (
        estimate["lock_state"]
        + estimate["lock_detail"]
        + estimate["credentials"]
        + estimate["gateway_status"]
        + estimate["door_sensor"]
    )


def test_scales_with_lock_count():
    one = estimate_monthly_calls({}, connectable_locks=1, locks_with_gateway=1)
    five = estimate_monthly_calls({}, connectable_locks=5, locks_with_gateway=5)

    # rounding happens on the aggregate, so compare against the exact math
    assert five["lock_state"] == round(5 * 48 * DAYS_PER_MONTH)
    # gateway/list is account-wide - it must NOT scale with lock count
    assert five["gateway_status"] == one["gateway_status"]


def test_longer_intervals_mean_fewer_calls():
    gentle = estimate_monthly_calls(
        {
            CONF_POLL_INTERVAL: 60,
            CONF_SLOW_POLL_INTERVAL: 12,
            CONF_GATEWAY_POLL_INTERVAL: 60,
        },
        connectable_locks=2,
        locks_with_gateway=2,
    )
    default = estimate_monthly_calls({}, connectable_locks=2, locks_with_gateway=2)

    assert gentle["total"] < default["total"]
    assert gentle["lock_state"] == round(2 * 24 * DAYS_PER_MONTH)
    assert gentle["gateway_status"] == round(24 * DAYS_PER_MONTH)


def test_webhook_only_zeroes_fast_tier_only_when_confirmed():
    confirmed = estimate_monthly_calls(
        {CONF_WEBHOOK_ONLY: True},
        connectable_locks=3,
        locks_with_gateway=3,
        webhook_confirmed=True,
    )
    unconfirmed = estimate_monthly_calls(
        {CONF_WEBHOOK_ONLY: True},
        connectable_locks=3,
        locks_with_gateway=3,
        webhook_confirmed=False,
    )

    assert confirmed["lock_state"] == 0
    assert confirmed["lock_detail"] > 0
    assert unconfirmed["lock_state"] > 0


def test_manual_sync_zeroes_everything():
    estimate = estimate_monthly_calls(
        {CONF_MANUAL_SYNC: True},
        connectable_locks=10,
        locks_with_gateway=10,
        locks_with_door_sensor=10,
    )
    assert estimate["total"] == 0


def test_door_sensor_daily_budget():
    estimate = estimate_monthly_calls(
        {}, connectable_locks=2, locks_with_gateway=2, locks_with_door_sensor=2
    )
    assert estimate["door_sensor"] == round(2 * DAYS_PER_MONTH)
