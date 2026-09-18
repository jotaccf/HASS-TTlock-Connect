"""Config flow for TTLock."""

from datetime import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateTimeSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util.dt import as_utc

from .const import (
    CONF_GATEWAY_POLL_INTERVAL,
    CONF_MANUAL_SYNC,
    CONF_POLL_INTERVAL,
    CONF_REGION,
    CONF_SLOW_POLL_INTERVAL,
    CONF_WEBHOOK_ONLY,
    CONF_WEBHOOK_STATUS,
    DEFAULT_GATEWAY_POLL_INTERVAL_MINUTES,
    DEFAULT_MANUAL_SYNC,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_REGION,
    DEFAULT_SLOW_POLL_INTERVAL_HOURS,
    DEFAULT_WEBHOOK_ONLY,
    DOMAIN,
    REGIONS,
    TT_LOCKS,
)
from .coordinator import LockUpdateCoordinator
from .models import AddPasscodeConfig, EkeyStatus, Features
from .usage_estimate import estimate_monthly_calls


class TTLockAuthFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle TTLock OAuth2 authentication."""

    DOMAIN = DOMAIN
    _region: str = DEFAULT_REGION

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a region and authenticate against that region's cloud."""
        # Flow has been triggered by external data
        errors = {}
        if user_input is not None:
            self._region = user_input[CONF_REGION]
            # TTLock runs separate per-region clouds; point the OAuth token
            # request at the chosen region before logging in. The runtime
            # implementation is rebuilt from entry.data[CONF_REGION] on setup
            # (see application_credentials.async_get_auth_implementation), so
            # this flow-time mutation only needs to hold for login() here.
            self.flow_impl.token_url = REGIONS[self._region]["token_url"]  # ty: ignore[unresolved-attribute] - flow_impl is a TTLockAuthImplementation
            session = await self.flow_impl.login(  # ty: ignore[unresolved-attribute] - flow_impl is a TTLockAuthImplementation
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if "errmsg" in session:
                errors["base"] = session["errmsg"]
            else:
                self.external_data = session
                return await self.async_step_creation()

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REGION, default=self._region): SelectSelector(
                        SelectSelectorConfig(
                            options=list(REGIONS),
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="region",
                        )
                    ),
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Persist the selected region alongside the OAuth token."""
        data[CONF_REGION] = self._region
        return await super().async_oauth_create_entry(data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for tuning polling cadence."""
        return TTLockOptionsFlow()


class TTLockOptionsFlow(OptionsFlow):
    """Options: polling cadence plus a PIN/eKey management panel.

    Two top-level branches from an initial menu:

    - "cadence": the polling knobs (fast/slow/gateway intervals, webhook-only
      and manual-sync modes). Saving reloads the entry so new coordinators
      pick the values up.
    - "codes": a management panel mirroring the TTLock app's flow - pick a
      lock, see its PIN codes and eKeys, then add/delete PINs and
      send/revoke/freeze/unfreeze eKeys through native HA forms. Every action
      goes straight to the cloud API and refreshes the lock's credential
      sensors; leaving via "finish" re-saves the options unchanged, which the
      reload guard in __init__.py recognizes as a no-op.
    """

    _coordinator: LockUpdateCoordinator | None = None

    def _estimate_placeholders(self) -> dict[str, str]:
        """Placeholders describing what the currently saved cadence costs.

        Computed from the loaded coordinators (device counts) and the saved
        options - the form can't recompute live as values are typed, so the
        description shows the cost of what's saved now; saving reloads the
        entry and reopening the form (or the Estimated Monthly sensor) shows
        the updated number.
        """
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        locks = entry_data.get(TT_LOCKS) if entry_data else None
        if locks is None:
            return {"locks": "?", "estimate": "?"}

        connectable = [coordinator for coordinator in locks if coordinator.connectable]
        estimate = estimate_monthly_calls(
            self.config_entry.options,
            connectable_locks=len(connectable),
            locks_with_gateway=sum(
                1 for coordinator in connectable if coordinator.has_gateway
            ),
            locks_with_door_sensor=sum(
                1
                for coordinator in connectable
                if Features.door_sensor in coordinator.data.features
            ),
            webhook_confirmed=bool(self.config_entry.data.get(CONF_WEBHOOK_STATUS)),
        )
        return {
            "locks": str(len(connectable)),
            "estimate": f"{estimate['total']:,}",
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Top-level menu: polling cadence or PIN/eKey management."""
        return self.async_show_menu(step_id="init", menu_options=["cadence", "codes"])

    async def async_step_cadence(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling-cadence options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="cadence",
            description_placeholders=self._estimate_placeholders(),
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MINUTES
                        ),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=5,
                                max=1440,
                                step=1,
                                unit_of_measurement="minutes",
                                mode=NumberSelectorMode.BOX,
                            )
                        ),
                        # NumberSelector yields floats; keep stored options as
                        # ints so the reload guard's snapshot compares cleanly.
                        vol.Coerce(int),
                    ),
                    vol.Required(
                        CONF_SLOW_POLL_INTERVAL,
                        default=options.get(
                            CONF_SLOW_POLL_INTERVAL, DEFAULT_SLOW_POLL_INTERVAL_HOURS
                        ),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=1,
                                max=168,
                                step=1,
                                unit_of_measurement="hours",
                                mode=NumberSelectorMode.BOX,
                            )
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Required(
                        CONF_GATEWAY_POLL_INTERVAL,
                        default=options.get(
                            CONF_GATEWAY_POLL_INTERVAL,
                            DEFAULT_GATEWAY_POLL_INTERVAL_MINUTES,
                        ),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=5,
                                max=1440,
                                step=1,
                                unit_of_measurement="minutes",
                                mode=NumberSelectorMode.BOX,
                            )
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Required(
                        CONF_WEBHOOK_ONLY,
                        default=options.get(CONF_WEBHOOK_ONLY, DEFAULT_WEBHOOK_ONLY),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_MANUAL_SYNC,
                        default=options.get(CONF_MANUAL_SYNC, DEFAULT_MANUAL_SYNC),
                    ): BooleanSelector(),
                }
            ),
        )

    # --- PIN / eKey management panel -------------------------------------

    def _connectable_coordinators(self) -> list[LockUpdateCoordinator] | None:
        """The entry's connectable lock coordinators, or None if not loaded."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        locks = entry_data.get(TT_LOCKS) if entry_data else None
        if locks is None:
            return None
        return [coordinator for coordinator in locks if coordinator.connectable]

    async def async_step_codes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which lock to manage."""
        coordinators = self._connectable_coordinators()
        if coordinators is None:
            return self.async_abort(reason="not_loaded")
        if not coordinators:
            return self.async_abort(reason="no_locks")

        if user_input is not None:
            self._coordinator = next(
                coordinator
                for coordinator in coordinators
                if str(coordinator.lock_id) == user_input["lock"]
            )
            return await self.async_step_lock_menu()

        if len(coordinators) == 1:
            self._coordinator = coordinators[0]
            return await self.async_step_lock_menu()

        return self.async_show_form(
            step_id="codes",
            data_schema=vol.Schema(
                {
                    vol.Required("lock"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=str(coordinator.lock_id),
                                    label=coordinator.data.name,
                                )
                                for coordinator in coordinators
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_lock_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The lock's overview: current PINs and eKeys, plus the actions."""
        coordinator = self._coordinator
        assert coordinator is not None

        # First entry for this lock: fetch the lists if the slow tier hasn't
        # yet. Actions re-fetch on completion, so re-showing the menu after
        # one costs no extra API calls.
        if coordinator.data.passcodes is None or coordinator.data.ekeys is None:
            await coordinator.async_refresh_codes()

        passcodes = coordinator.data.passcodes or []
        ekeys = coordinator.data.ekeys or []

        menu = ["add_pin"]
        if passcodes:
            menu.append("delete_pin")
        menu.append("send_ekey")
        if ekeys:
            menu.append("delete_ekey")
        if any(ekey.status is EkeyStatus.normal for ekey in ekeys):
            menu.append("freeze_ekey")
        if any(ekey.status is EkeyStatus.frozen for ekey in ekeys):
            menu.append("unfreeze_ekey")
        menu.append("finish")

        return self.async_show_menu(
            step_id="lock_menu",
            menu_options=menu,
            description_placeholders={
                "lock": coordinator.data.name,
                "pins": self._pins_overview(passcodes),
                "ekeys": self._ekeys_overview(ekeys),
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Close the panel; options unchanged, so no reload happens."""
        return self.async_create_entry(data=dict(self.config_entry.options))

    @staticmethod
    def _format_period(
        start: datetime | None, end: datetime | None, expired: bool
    ) -> str:
        """Compact validity-window text for the overview lists."""
        if start is None and end is None:
            return "∞"
        fmt = "%Y-%m-%d %H:%M"
        text = (
            f"{start.strftime(fmt) if start else '…'}"
            f" → {end.strftime(fmt) if end else '…'}"
        )
        return f"{text} ⚠" if expired else text

    def _pins_overview(self, passcodes: list) -> str:
        """Markdown list of the lock's PIN codes for the menu description."""
        if not passcodes:
            return "—"
        return "\n".join(
            f"- **{code.name}** · `{code.passcode}` · "
            f"{self._format_period(code.start_date, code.end_date, code.expired)}"
            for code in passcodes
        )

    def _ekeys_overview(self, ekeys: list) -> str:
        """Markdown list of the lock's eKeys for the menu description."""
        if not ekeys:
            return "—"
        status_icons = {
            EkeyStatus.normal: "✓",
            EkeyStatus.pending: "⏳",
            EkeyStatus.frozen: "❄",
        }
        return "\n".join(
            f"- **{ekey.name}** · {ekey.username} · "
            f"{status_icons.get(ekey.status, ekey.status.name)} · "
            f"{self._format_period(ekey.start_date, ekey.end_date, ekey.expired)}"
            for ekey in ekeys
        )

    @staticmethod
    def _period_ms(
        start: datetime | None, end: datetime | None
    ) -> tuple[int, int] | None:
        """Epoch-ms pair for a validity window, or None if only one end given."""
        if (start is None) != (end is None):
            return None
        if start is None:
            return (0, 0)
        assert end is not None
        return (
            int(as_utc(start).timestamp() * 1000),
            int(as_utc(end).timestamp() * 1000),
        )

    async def async_step_add_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a new PIN code on the lock."""
        coordinator = self._coordinator
        assert coordinator is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            period = self._period_ms(
                user_input.get("start_time"), user_input.get("end_time")
            )
            passcode = user_input["passcode"]
            if not (passcode.isdigit() and 4 <= len(passcode) <= 9):
                errors["passcode"] = "invalid_passcode"
            elif period is None:
                errors["base"] = "period_incomplete"
            else:
                start_ms, end_ms = period
                created = await coordinator.api.add_passcode(
                    coordinator.lock_id,
                    AddPasscodeConfig(
                        passcode=passcode,
                        passcodeName=user_input["passcode_name"],
                        startDate=start_ms or None,
                        endDate=end_ms or None,
                    ),
                )
                if created:
                    await coordinator.async_refresh_codes()
                    return await self.async_step_lock_menu()
                errors["base"] = "api_error"

        return self.async_show_form(
            step_id="add_pin",
            errors=errors,
            description_placeholders={"lock": coordinator.data.name},
            data_schema=vol.Schema(
                {
                    vol.Required("passcode_name"): str,
                    vol.Required("passcode"): str,
                    vol.Optional("start_time"): vol.All(
                        DateTimeSelector(), cv.datetime
                    ),
                    vol.Optional("end_time"): vol.All(DateTimeSelector(), cv.datetime),
                }
            ),
        )

    async def async_step_delete_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete one of the lock's PIN codes."""
        coordinator = self._coordinator
        assert coordinator is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            deleted = await coordinator.api.delete_passcode(
                coordinator.lock_id, int(user_input["passcode"])
            )
            if deleted:
                await coordinator.async_refresh_codes()
                return await self.async_step_lock_menu()
            errors["base"] = "api_error"

        options = [
            SelectOptionDict(value=str(code.id), label=f"{code.name} ({code.passcode})")
            for code in coordinator.data.passcodes or []
            if code.id is not None
        ]
        if not options:
            return await self.async_step_lock_menu()

        return self.async_show_form(
            step_id="delete_pin",
            errors=errors,
            description_placeholders={"lock": coordinator.data.name},
            data_schema=vol.Schema(
                {
                    vol.Required("passcode"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_send_ekey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send an eKey for the lock to another TTLock account."""
        coordinator = self._coordinator
        assert coordinator is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            period = self._period_ms(
                user_input.get("start_time"), user_input.get("end_time")
            )
            if period is None:
                errors["base"] = "period_incomplete"
            else:
                start_ms, end_ms = period
                key_id = await coordinator.api.send_ekey(
                    coordinator.lock_id,
                    receiver_username=user_input["receiver_username"],
                    name=user_input["name"],
                    start_ms=start_ms,
                    end_ms=end_ms,
                    remote_enable=user_input.get("remote_enable"),
                    create_user=user_input.get("create_user", False),
                )
                if key_id is not None:
                    await coordinator.async_refresh_codes()
                    return await self.async_step_lock_menu()
                errors["base"] = "api_error"

        return self.async_show_form(
            step_id="send_ekey",
            errors=errors,
            description_placeholders={"lock": coordinator.data.name},
            data_schema=vol.Schema(
                {
                    vol.Required("receiver_username"): str,
                    vol.Required("name"): str,
                    vol.Optional("start_time"): vol.All(
                        DateTimeSelector(), cv.datetime
                    ),
                    vol.Optional("end_time"): vol.All(DateTimeSelector(), cv.datetime),
                    vol.Optional("remote_enable", default=False): BooleanSelector(),
                    vol.Optional("create_user", default=False): BooleanSelector(),
                }
            ),
        )

    def _ekey_choices(self, statuses: set[EkeyStatus] | None = None) -> list:
        """Selector options for the lock's eKeys, optionally by status."""
        coordinator = self._coordinator
        assert coordinator is not None
        return [
            SelectOptionDict(value=str(ekey.id), label=f"{ekey.name} — {ekey.username}")
            for ekey in coordinator.data.ekeys or []
            if ekey.id is not None and (statuses is None or ekey.status in statuses)
        ]

    async def _ekey_action_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        action,
        statuses: set[EkeyStatus] | None = None,
    ) -> ConfigFlowResult:
        """Shared pick-an-eKey-and-act form (delete/freeze/unfreeze)."""
        coordinator = self._coordinator
        assert coordinator is not None

        if user_input is not None:
            await action(int(user_input["ekey"]))
            await coordinator.async_refresh_codes()
            return await self.async_step_lock_menu()

        options = self._ekey_choices(statuses)
        if not options:
            return await self.async_step_lock_menu()

        return self.async_show_form(
            step_id=step_id,
            description_placeholders={"lock": coordinator.data.name},
            data_schema=vol.Schema(
                {
                    vol.Required("ekey"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_delete_ekey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Revoke one of the lock's eKeys."""
        assert self._coordinator is not None
        return await self._ekey_action_step(
            "delete_ekey", user_input, self._coordinator.api.delete_ekey
        )

    async def async_step_freeze_ekey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Temporarily disable one of the lock's eKeys."""
        assert self._coordinator is not None
        return await self._ekey_action_step(
            "freeze_ekey",
            user_input,
            self._coordinator.api.freeze_ekey,
            {EkeyStatus.normal},
        )

    async def async_step_unfreeze_ekey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enable one of the lock's frozen eKeys."""
        assert self._coordinator is not None
        return await self._ekey_action_step(
            "unfreeze_ekey",
            user_input,
            self._coordinator.api.unfreeze_ekey,
            {EkeyStatus.frozen},
        )
