"""Validated per-host options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_ENABLE_DANGEROUS_CONTROLS,
    CONF_ENABLE_PACKAGE_CHECKS,
    CONF_FILESYSTEM_EXCLUDE,
    CONF_FILESYSTEM_INCLUDE,
    CONF_MONITORED_SERVICES,
    CONF_NETWORK_EXCLUDE,
    CONF_NETWORK_INCLUDE,
    CONF_PACKAGE_CHECK_CADENCE,
    CONF_POLL_INTERVAL,
    CONF_TEMPERATURE_WARNING,
    DEFAULT_OPTIONS,
    DOMAIN,
    MIN_POLL_INTERVAL,
)
from .errors import PiManagerError, ServiceNameError
from .validation import validate_service_list


class PiManagerOptionsFlow(config_entries.OptionsFlowWithReload):
    """Edit behaviour without changing a host's identity or key."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_POLL_INTERVAL] = _validate_poll(user_input[CONF_POLL_INTERVAL])
                user_input[CONF_MONITORED_SERVICES] = list(
                    validate_service_list(user_input.get(CONF_MONITORED_SERVICES, []))
                )
                runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if runtime is not None:
                    for service in user_input[CONF_MONITORED_SERVICES]:
                        await runtime.async_validate_service(service)
                    await runtime.async_configure_services(user_input[CONF_MONITORED_SERVICES], user_input)
                return self.async_create_entry(title="", data=user_input)
            except ServiceNameError as err:
                errors["base"] = str(err)
            except PiManagerError:
                errors["base"] = "service_unavailable"
            except TypeError, ValueError:
                errors["base"] = "invalid_options"
        schema = _options_schema({**DEFAULT_OPTIONS, **self.config_entry.options})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    string_list = selector.TextSelector(selector.TextSelectorConfig(multiple=True))
    return vol.Schema(
        {
            vol.Required(CONF_POLL_INTERVAL, default=current[CONF_POLL_INTERVAL]): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=3600)
            ),
            vol.Optional(CONF_FILESYSTEM_INCLUDE, default=current[CONF_FILESYSTEM_INCLUDE]): string_list,
            vol.Optional(CONF_FILESYSTEM_EXCLUDE, default=current[CONF_FILESYSTEM_EXCLUDE]): string_list,
            vol.Optional(CONF_NETWORK_INCLUDE, default=current[CONF_NETWORK_INCLUDE]): string_list,
            vol.Optional(CONF_NETWORK_EXCLUDE, default=current[CONF_NETWORK_EXCLUDE]): string_list,
            vol.Optional(CONF_MONITORED_SERVICES, default=current[CONF_MONITORED_SERVICES]): string_list,
            vol.Required(CONF_ENABLE_PACKAGE_CHECKS, default=current[CONF_ENABLE_PACKAGE_CHECKS]): bool,
            vol.Required(CONF_PACKAGE_CHECK_CADENCE, default=current[CONF_PACKAGE_CHECK_CADENCE]): vol.All(
                vol.Coerce(int), vol.Range(min=900, max=7 * 24 * 60 * 60)
            ),
            vol.Required(CONF_TEMPERATURE_WARNING, default=current[CONF_TEMPERATURE_WARNING]): vol.All(
                vol.Coerce(float), vol.Range(min=40, max=120)
            ),
            vol.Required(CONF_ENABLE_DANGEROUS_CONTROLS, default=current[CONF_ENABLE_DANGEROUS_CONTROLS]): bool,
        }
    )


def _validate_poll(value: Any) -> int:
    interval = int(value)
    if interval < MIN_POLL_INTERVAL:
        raise ValueError("poll_interval_invalid")
    return interval
