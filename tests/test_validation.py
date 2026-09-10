from __future__ import annotations

import pytest

from custom_components.pi_manager.errors import ServiceNameError
from custom_components.pi_manager.validation import (
    validate_host,
    validate_port,
    validate_service_allowlist,
    validate_service_list,
    validate_service_name,
)


@pytest.mark.parametrize("name", ["smbd.service", "home-assistant.service", "serial-getty@ttyUSB0.service"])
def test_valid_service_names(name: str) -> None:
    assert validate_service_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "smbd;reboot",
        "../../something.service",
        "smbd.service && reboot",
        "$(reboot).service",
        "smbd.service\nreboot",
        " smbd.service",
        "smbd",
    ],
)
def test_service_injection_is_rejected(name: str) -> None:
    with pytest.raises(ServiceNameError):
        validate_service_name(name)


def test_service_allowlist_is_required() -> None:
    assert validate_service_allowlist("smbd.service", {"smbd.service"}) == "smbd.service"
    with pytest.raises(ServiceNameError, match="not_allowlisted"):
        validate_service_allowlist("cron.service", {"smbd.service"})


def test_service_list_is_deduplicated() -> None:
    assert validate_service_list(["smbd.service", "smbd.service"]) == ("smbd.service",)


def test_host_and_port_validation() -> None:
    assert validate_host("pi-nas.local") == "pi-nas.local"
    assert validate_host("192.0.2.12") == "192.0.2.12"
    assert validate_port("22") == 22
    with pytest.raises(ValueError):
        validate_host("bad host")
    with pytest.raises(ValueError):
        validate_port(70000)
