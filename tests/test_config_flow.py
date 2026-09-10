from __future__ import annotations

import json

import pytest

from custom_components.pi_manager.config_flow import PiManagerConfigFlow, _error_key, _user_schema
from custom_components.pi_manager.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from custom_components.pi_manager.errors import BootstrapError, HostDiscoveryError


def test_connection_schema_collects_bootstrap_fields() -> None:
    data = _user_schema()({CONF_HOST: "pi.example", CONF_PORT: 22, CONF_USERNAME: "ha", CONF_PASSWORD: "secret"})
    assert data[CONF_HOST] == "pi.example"
    assert data[CONF_PORT] == 22
    assert data[CONF_USERNAME] == "ha"
    assert data[CONF_PASSWORD] == "secret"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BootstrapError("authentication"), "wrong_password"),
        (BootstrapError("target_read_only"), "read_only_target"),
        (HostDiscoveryError("missing_apt"), "missing_apt"),
        (ValueError("username_invalid"), "username_invalid"),
    ],
)
def test_config_flow_maps_actionable_error_categories(error: Exception, expected: str) -> None:
    assert _error_key(error) == expected


def test_success_entry_shape_does_not_include_bootstrap_password() -> None:
    flow = PiManagerConfigFlow()
    result = flow.async_create_entry(
        title="Pi NAS",
        data={
            "host": "pi.example",
            "port": 22,
            "username": "ha",
            "machine_id": "a" * 32,
            "fingerprint": "SHA256:trusted",
            "key_id": "a" * 32,
            "helper_version": "0.1.0",
        },
    )
    assert result["type"] == "create_entry"
    assert "password" not in result["data"]
    assert "secret" not in json.dumps(result["data"])
