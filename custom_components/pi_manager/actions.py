"""Explicit management action dispatch for one runtime."""

from __future__ import annotations

from typing import Any

from .const import CONF_ENABLE_DANGEROUS_CONTROLS
from .errors import PiManagerError


async def async_refresh(runtime: Any) -> None:
    await runtime.coordinator.async_request_refresh()


async def async_check_updates(runtime: Any) -> None:
    await runtime.async_check_updates()
    await runtime.coordinator.async_request_refresh()


async def async_preview_upgrade(runtime: Any) -> None:
    await runtime.async_preview_upgrade()
    await runtime.coordinator.async_request_refresh()


async def async_preview_dist_upgrade(runtime: Any) -> None:
    await runtime.async_preview_dist_upgrade()
    await runtime.coordinator.async_request_refresh()


async def async_preview_autoremove(runtime: Any) -> None:
    await runtime.async_preview_autoremove()
    await runtime.coordinator.async_request_refresh()


async def async_audit_packages(runtime: Any) -> None:
    await runtime.async_audit_packages()
    await runtime.coordinator.async_request_refresh()


async def async_show_package_holds(runtime: Any) -> None:
    await runtime.async_show_package_holds()
    await runtime.coordinator.async_request_refresh()


async def async_failed_services(runtime: Any) -> None:
    await runtime.async_failed_services()
    await runtime.coordinator.async_request_refresh()


async def async_install_updates(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_update()
    await runtime.coordinator.async_request_refresh()


async def async_dist_upgrade(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_dist_upgrade()
    await runtime.coordinator.async_request_refresh()


async def async_configure_packages(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_configure_packages()
    await runtime.coordinator.async_request_refresh()


async def async_repair_packages(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_repair_packages()
    await runtime.coordinator.async_request_refresh()


async def async_autoremove(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_autoremove()
    await runtime.coordinator.async_request_refresh()


async def async_autoclean(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_autoclean()
    await runtime.coordinator.async_request_refresh()


async def async_clean_cache(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_start_clean_cache()
    await runtime.coordinator.async_request_refresh()


async def async_reboot(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_reboot()


async def async_shutdown(runtime: Any) -> None:
    _require_dangerous(runtime)
    await runtime.async_shutdown()


async def async_restart_service(runtime: Any, name: str) -> None:
    _require_dangerous(runtime)
    await runtime.async_restart_service(name)
    await runtime.coordinator.async_request_refresh()


def _require_dangerous(runtime: Any) -> None:
    if not runtime.options.get(CONF_ENABLE_DANGEROUS_CONTROLS, False):
        raise PiManagerError("dangerous_controls_disabled")
