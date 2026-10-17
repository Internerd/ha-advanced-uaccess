"""Config flow for the Advanced UniFi Access Directory integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import create_client
from .api import AccessApiError, AccessAuthError, AccessConnectionError
from .const import (
    CONF_RESTORE_SCHEDULES,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_CALLS,
    CONF_USER_RECEIVERS,
    CONF_VERIFY_SSL,
    DEFAULT_RESTORE_SCHEDULES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRACK_CALLS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TYPE_ADMIN,
)
from .coordinator import AccessConfigEntry

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): BooleanSelector(),
        }
    )


class AccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a UniFi OS console."""

    VERSION = 1

    async def _validate(self, data: Mapping[str, Any]) -> dict[str, str]:
        """Log in and list intercoms; return form errors."""
        client = create_client(self.hass, dict(data))
        try:
            await client.login()
            callers = await client.get_callers()
        except AccessAuthError:
            return {"base": "invalid_auth"}
        except AccessConnectionError as err:
            _LOGGER.debug("Connection to %s failed: %s", data[CONF_HOST], err)
            return {"base": "cannot_connect"}
        except AccessApiError as err:
            _LOGGER.debug("Access API error: %s", err)
            return {"base": "no_access"}
        except Exception:
            _LOGGER.exception("Unexpected error during setup")
            return {"base": "unknown"}
        if not callers:
            return {"base": "no_intercom"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for host and local account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_configured()
            errors = await self._validate(user_input)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_HOST], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors = await self._validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, account or SSL setting."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_mismatch(reason="wrong_console")
            errors = await self._validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AccessConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AccessOptionsFlow()


class AccessOptionsFlow(OptionsFlow):
    """Polling interval, schedule restore and user receivers."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        coordinator = getattr(self.config_entry, "runtime_data", None)
        users: dict[str, str] = {}
        current_users: set[str] = set()
        if coordinator is not None and coordinator.data is not None:
            users = coordinator.data.catalog.get(TYPE_ADMIN, {})
            for caller in coordinator.data.callers.values():
                for target in (caller.direct, *caller.rooms.values()):
                    current_users.update(r.id for r in target.receivers if r.type == TYPE_ADMIN)
        default_users = options.get(CONF_USER_RECEIVERS, sorted(current_users))

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_RESTORE_SCHEDULES,
                    default=options.get(CONF_RESTORE_SCHEDULES, DEFAULT_RESTORE_SCHEDULES),
                ): BooleanSelector(),
                vol.Required(
                    CONF_TRACK_CALLS,
                    default=options.get(CONF_TRACK_CALLS, DEFAULT_TRACK_CALLS),
                ): BooleanSelector(),
                vol.Optional(CONF_USER_RECEIVERS, default=default_users): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=uid, label=name)
                            for uid, name in sorted(users.items(), key=lambda item: item[1].lower())
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
