from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.pi_manager.const import DEFAULT_OPTIONS
from custom_components.pi_manager.options_flow import _options_schema


def test_options_schema_accepts_minimum_safe_polling_interval() -> None:
    schema = _options_schema({**DEFAULT_OPTIONS})
    result = schema({**DEFAULT_OPTIONS, "poll_interval": 15})
    assert result["poll_interval"] == 15


def test_options_schema_rejects_polling_below_fifteen_seconds() -> None:
    schema = _options_schema({**DEFAULT_OPTIONS})
    with pytest.raises(vol.Invalid):
        schema({**DEFAULT_OPTIONS, "poll_interval": 14})
