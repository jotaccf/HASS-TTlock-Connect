"""Test the persisted API-call counter."""

from datetime import datetime

from freezegun import freeze_time
import pytest

from custom_components.ttlock.api_stats import ApiCallCounter


@pytest.fixture
async def counter(hass):
    """A loaded, empty counter."""
    counter = ApiCallCounter(hass)
    await counter.async_load()
    return counter


async def test_starts_at_zero(counter: ApiCallCounter):
    assert counter.today_count == 0
    assert counter.month_count == 0
    assert counter.today_by_endpoint() == {}
    assert counter.month_by_endpoint() == {}


async def test_record_counts_today_and_month(counter: ApiCallCounter):
    counter.record("lock/queryOpenState")
    counter.record("lock/queryOpenState")
    counter.record("gateway/list")

    assert counter.today_count == 3
    assert counter.month_count == 3
    assert counter.today_by_endpoint() == {
        "lock/queryOpenState": 2,
        "gateway/list": 1,
    }


async def test_month_spans_days_but_not_months(hass):
    counter = ApiCallCounter(hass)
    await counter.async_load()

    with freeze_time(datetime(2026, 8, 31, 12, 0)):
        counter.record("gateway/list")
    with freeze_time(datetime(2026, 9, 1, 12, 0)):
        counter.record("gateway/list")
    with freeze_time(datetime(2026, 9, 2, 12, 0)):
        counter.record("lock/detail")

        assert counter.today_count == 1
        # August's call is excluded from September's total
        assert counter.month_count == 2
        assert counter.month_by_endpoint() == {"gateway/list": 1, "lock/detail": 1}


async def test_projection_extrapolates_month_to_date_rate(hass):
    counter = ApiCallCounter(hass)
    await counter.async_load()

    # 10 calls by the end of day 10 of a 30-day month -> ~1/day -> ~30.
    # freeze_time takes UTC; the test hass runs in US/Pacific (UTC-7), so
    # 07:00Z is local midnight starting day 11 - exactly 10 days elapsed.
    with freeze_time(datetime(2026, 9, 11, 7, 0)):
        for _ in range(10):
            counter.record("lock/queryOpenState")
        assert counter.projected_month_count == 30


async def test_projection_is_tally_at_month_start(hass):
    """Too little of the month elapsed - no wild extrapolation."""
    counter = ApiCallCounter(hass)
    await counter.async_load()

    # 09:00Z = 02:00 local (US/Pacific) on the 1st - under the 6h threshold
    with freeze_time(datetime(2026, 9, 1, 9, 0)):
        counter.record("lock/queryOpenState")
        assert counter.projected_month_count == 1


async def test_counts_persist_across_instances(hass):
    """A new counter - as happens on HA restart - must see saved tallies."""
    first = ApiCallCounter(hass)
    await first.async_load()
    first.record("gateway/list")
    # counts are debounce-saved; flush the pending write like HA's stop does
    await first.async_flush()

    second = ApiCallCounter(hass)
    await second.async_load()
    assert second.today_count == 1


async def test_old_days_are_pruned(hass):
    counter = ApiCallCounter(hass)
    await counter.async_load()

    with freeze_time(datetime(2026, 1, 1, 12, 0)):
        counter.record("gateway/list")

    # well past RETENTION_DAYS - the next record prunes the January day
    with freeze_time(datetime(2026, 6, 1, 12, 0)):
        counter.record("gateway/list")
        assert counter.today_count == 1

    # the pruned day now reads as zero, even "back in time"
    with freeze_time(datetime(2026, 1, 1, 12, 0)):
        assert counter.today_count == 0
