"""Test the Sync Now button."""

from unittest.mock import AsyncMock

from custom_components.ttlock_connect.api import TTLockApi


async def test_sync_now_button_exists(hass, component_setup, mock_api_responses):
    mock_api_responses("default")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("button.ttlock_sync_now") is not None


async def test_sync_now_refreshes_locks_and_gateways(
    hass, component_setup, mock_api_responses, monkeypatch
):
    """One press re-fetches lock state and the gateway list."""
    mock_api_responses("default")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    get_state = AsyncMock(side_effect=TTLockApi.get_lock_state)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.get_lock_state", get_state
    )
    get_gateways = AsyncMock(side_effect=TTLockApi.get_gateways)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.get_gateways", get_gateways
    )

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.ttlock_sync_now"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert get_state.call_count == 1
    assert get_gateways.call_count == 1


async def test_per_lock_sync_button_exists(hass, component_setup, mock_api_responses):
    mock_api_responses("default")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("button.front_door_sync") is not None


async def test_per_lock_sync_refreshes_only_that_lock(
    hass, component_setup, mock_api_responses, monkeypatch
):
    """The per-lock button re-fetches its lock's state but not the gateways."""
    mock_api_responses("default")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    get_state = AsyncMock(side_effect=TTLockApi.get_lock_state)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.get_lock_state", get_state
    )
    get_gateways = AsyncMock(side_effect=TTLockApi.get_gateways)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.get_gateways", get_gateways
    )

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.front_door_sync"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert get_state.call_count == 1
    assert get_gateways.call_count == 0
