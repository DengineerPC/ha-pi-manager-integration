from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
from probatio import to_field_list
from probatio.error import Invalid

from custom_components.pi_manager.const import (
    CONF_FILESYSTEM_EXCLUDE,
    CONF_FILESYSTEM_INCLUDE,
    CONF_MONITORED_SERVICES,
    CONF_NETWORK_EXCLUDE,
    CONF_NETWORK_INCLUDE,
    DEFAULT_OPTIONS,
)
from custom_components.pi_manager.options_flow import PiManagerOptionsFlow, _options_schema


def test_options_flow_uses_explicit_reload_listener_compatible_base() -> None:
    assert not issubclass(PiManagerOptionsFlow, config_entries.OptionsFlowWithReload)


def test_options_schema_accepts_minimum_safe_polling_interval() -> None:
    schema = _options_schema({**DEFAULT_OPTIONS})
    result = schema({**DEFAULT_OPTIONS, "poll_interval": 15})
    assert result["poll_interval"] == 15


def test_options_schema_rejects_polling_below_fifteen_seconds() -> None:
    schema = _options_schema({**DEFAULT_OPTIONS})
    with pytest.raises(Invalid):
        schema({**DEFAULT_OPTIONS, "poll_interval": 14})


def test_options_schema_serializes_and_preserves_list_values() -> None:
    schema = _options_schema({**DEFAULT_OPTIONS})
    fields = to_field_list(schema, custom_serializer=cv.custom_serializer)
    fields_by_name = {field["name"]: field for field in fields}
    list_fields = (
        CONF_FILESYSTEM_INCLUDE,
        CONF_FILESYSTEM_EXCLUDE,
        CONF_NETWORK_INCLUDE,
        CONF_NETWORK_EXCLUDE,
        CONF_MONITORED_SERVICES,
    )

    for name in list_fields:
        assert fields_by_name[name]["selector"]["text"]["multiple"] is True

    submitted = {
        **DEFAULT_OPTIONS,
        CONF_MONITORED_SERVICES: ["tailscaled.service"],
    }
    validated = schema(submitted)
    assert validated[CONF_MONITORED_SERVICES] == ["tailscaled.service"]

    with pytest.raises(Invalid):
        schema({**DEFAULT_OPTIONS, CONF_MONITORED_SERVICES: "tailscaled.service"})
