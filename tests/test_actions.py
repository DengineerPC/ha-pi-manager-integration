from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.pi_manager import actions
from custom_components.pi_manager.errors import PiManagerError, ServiceNameError
from custom_components.pi_manager.runtime import PiManagerRuntime


@pytest.mark.asyncio
async def test_dangerous_actions_require_explicit_option() -> None:
    runtime = SimpleNamespace(options={"enable_dangerous_controls": False})
    with pytest.raises(PiManagerError, match="dangerous_controls_disabled"):
        await actions.async_reboot(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_name",
    (
        "async_install_updates",
        "async_dist_upgrade",
        "async_configure_packages",
        "async_repair_packages",
        "async_autoremove",
        "async_autoclean",
        "async_clean_cache",
    ),
)
async def test_every_package_change_requires_explicit_option(action_name: str) -> None:
    runtime = SimpleNamespace(options={"enable_dangerous_controls": False})
    with pytest.raises(PiManagerError, match="dangerous_controls_disabled"):
        await getattr(actions, action_name)(runtime)


@pytest.mark.asyncio
async def test_service_restart_rejects_injection_before_runtime_call() -> None:
    runtime = PiManagerRuntime(
        hass=None,
        entry=SimpleNamespace(data={}, options={}),
        key_store=object(),
    )
    with pytest.raises(ServiceNameError):
        await runtime.async_restart_service("smbd.service;reboot")


@pytest.mark.asyncio
async def test_service_restart_requires_dangerous_option() -> None:
    runtime = SimpleNamespace(options={"enable_dangerous_controls": False})
    with pytest.raises(PiManagerError, match="dangerous_controls_disabled"):
        await actions.async_restart_service(runtime, "smbd.service")
