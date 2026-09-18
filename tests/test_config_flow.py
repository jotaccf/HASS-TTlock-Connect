"""Test the TTLock config flow region selection."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ttlock_connect.api import TTLockAuthImplementation
from custom_components.ttlock_connect.const import (
    CONF_GATEWAY_POLL_INTERVAL,
    CONF_MANUAL_SYNC,
    CONF_POLL_INTERVAL,
    CONF_REGION,
    CONF_SLOW_POLL_INTERVAL,
    CONF_WEBHOOK_ONLY,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

TOKEN = {
    "access_token": "access",
    "refresh_token": "refresh",
    "expires_in": 7776000,
    "token_type": "Bearer",
    "scope": "",
}


@pytest.mark.parametrize(
    ("region", "token_url"),
    [
        ("eu", "https://euapi.ttlock.com/oauth2/token"),
        ("cn", "https://cnapi.ttlock.com/oauth2/token"),
    ],
)
async def test_flow_persists_region_and_targets_its_token_url(
    hass: HomeAssistant, region: str, token_url: str
):
    """Selecting a region persists it and points login at that region's cloud."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential("client-id", "client-secret"), "mocked"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    if result["step_id"] == "pick_implementation":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"implementation": "mocked"}
        )
    assert result["step_id"] == "auth"
    captured: dict[str, str] = {}

    async def fake_token_request(self: TTLockAuthImplementation, data: dict) -> dict:
        captured["token_url"] = self.token_url
        return dict(TOKEN)

    with (
        patch.object(TTLockAuthImplementation, "_token_request", fake_token_request),
        patch("custom_components.ttlock_connect.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_REGION: region,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REGION] == region
    assert captured["token_url"] == token_url


async def test_options_flow_saves_polling_cadence(hass: HomeAssistant):
    """The options flow persists the fast/slow polling intervals."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cadence"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cadence"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLL_INTERVAL: 45,
            CONF_SLOW_POLL_INTERVAL: 12,
            CONF_GATEWAY_POLL_INTERVAL: 60,
            CONF_WEBHOOK_ONLY: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # coerced to int (NumberSelector yields floats) so the reload guard's
    # snapshot comparison stays clean
    assert entry.options[CONF_POLL_INTERVAL] == 45
    assert isinstance(entry.options[CONF_POLL_INTERVAL], int)
    assert entry.options[CONF_SLOW_POLL_INTERVAL] == 12
    assert isinstance(entry.options[CONF_SLOW_POLL_INTERVAL], int)
    assert entry.options[CONF_GATEWAY_POLL_INTERVAL] == 60
    assert isinstance(entry.options[CONF_GATEWAY_POLL_INTERVAL], int)
    assert entry.options[CONF_WEBHOOK_ONLY] is True


async def test_options_flow_defaults_new_fields(hass: HomeAssistant):
    """Omitting the new fields falls back to their defaults."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cadence"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLL_INTERVAL: 30, CONF_SLOW_POLL_INTERVAL: 6},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GATEWAY_POLL_INTERVAL] == 15
    assert entry.options[CONF_WEBHOOK_ONLY] is False
    assert entry.options[CONF_MANUAL_SYNC] is False


async def test_options_codes_panel_delete_pin(
    hass: HomeAssistant, component_setup, mock_api_responses, monkeypatch, config_entry
):
    """Menu -> manage codes -> delete a PIN -> back at the lock menu."""
    mock_api_responses("with_credentials")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.delete_passcode", delete
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    # single lock: goes straight to its menu, with the overview rendered
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "lock_menu"
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    assert "Cleaner" in placeholders["pins"]
    assert "Family" in placeholders["ekeys"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_pin"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "delete_pin"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"passcode": "111"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "lock_menu"
    assert delete.call_args.args == (7252408, 111)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_codes_panel_add_pin_validates_then_creates(
    hass: HomeAssistant, component_setup, mock_api_responses, monkeypatch, config_entry
):
    """A malformed PIN shows an error; a valid one hits the API."""
    mock_api_responses("with_credentials")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    add = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.add_passcode", add
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_pin"}
    )
    assert result["step_id"] == "add_pin"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"passcode_name": "Guest 2", "passcode": "12"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"passcode": "invalid_passcode"}
    assert add.call_count == 0

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"passcode_name": "Guest 2", "passcode": "246810"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "lock_menu"
    config = add.call_args.args[1]
    assert config.passcode == "246810"
    assert config.passcode_name == "Guest 2"
    assert config.start_minute is None


async def test_options_codes_panel_send_ekey(
    hass: HomeAssistant, component_setup, mock_api_responses, monkeypatch, config_entry
):
    """Sending an eKey from the panel calls the API with the receiver."""
    mock_api_responses("with_credentials")
    await component_setup()
    await hass.async_block_till_done(wait_background_tasks=True)

    send = AsyncMock(return_value=999)
    monkeypatch.setattr(
        "custom_components.ttlock_connect.api.TTLockApi.send_ekey", send
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "send_ekey"}
    )
    assert result["step_id"] == "send_ekey"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "receiver_username": "guest@example.com",
            "name": "Guest key",
            "remote_enable": True,
            "create_user": False,
        },
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "lock_menu"
    assert send.call_args.kwargs["receiver_username"] == "guest@example.com"
    assert send.call_args.kwargs["start_ms"] == 0
    assert send.call_args.kwargs["remote_enable"] is True
