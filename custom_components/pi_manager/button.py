"""Explicit Pi Manager maintenance buttons."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import actions
from .const import CONF_ENABLE_DANGEROUS_CONTROLS, CONF_MONITORED_SERVICES
from .device import device_info, slug


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    entities: list[ButtonEntity] = [
        PiManagerActionButton(runtime, "refresh", "Refresh", actions.async_refresh),
        PiManagerActionButton(runtime, "check_updates", "Check updates", actions.async_check_updates),
        PiManagerActionButton(runtime, "preview_upgrade", "Preview normal upgrade", actions.async_preview_upgrade),
        PiManagerActionButton(
            runtime,
            "preview_dist_upgrade",
            "Preview dependency upgrade",
            actions.async_preview_dist_upgrade,
        ),
        PiManagerActionButton(
            runtime,
            "preview_autoremove",
            "Preview unused dependencies",
            actions.async_preview_autoremove,
        ),
        PiManagerActionButton(runtime, "audit_packages", "Audit package database", actions.async_audit_packages),
        PiManagerActionButton(runtime, "show_package_holds", "Show held packages", actions.async_show_package_holds),
        PiManagerActionButton(runtime, "failed_services", "Check failed services", actions.async_failed_services),
    ]
    if runtime.options.get(CONF_ENABLE_DANGEROUS_CONTROLS, False):
        entities.extend(
            [
                PiManagerActionButton(runtime, "install_updates", "Install updates", actions.async_install_updates),
                PiManagerActionButton(
                    runtime,
                    "dist_upgrade",
                    "Dependency-changing upgrade",
                    actions.async_dist_upgrade,
                ),
                PiManagerActionButton(
                    runtime,
                    "configure_packages",
                    "Finish package configuration",
                    actions.async_configure_packages,
                ),
                PiManagerActionButton(
                    runtime,
                    "repair_packages",
                    "Repair broken dependencies",
                    actions.async_repair_packages,
                ),
                PiManagerActionButton(runtime, "autoremove", "Remove unused dependencies", actions.async_autoremove),
                PiManagerActionButton(runtime, "autoclean", "Clean obsolete package cache", actions.async_autoclean),
                PiManagerActionButton(
                    runtime,
                    "clean_cache",
                    "Clear downloaded package cache",
                    actions.async_clean_cache,
                ),
                PiManagerActionButton(runtime, "reboot", "Reboot", actions.async_reboot),
                PiManagerActionButton(runtime, "shutdown", "Shutdown", actions.async_shutdown),
            ]
        )
        entities.extend(
            PiManagerServiceRestartButton(runtime, service)
            for service in runtime.options.get(CONF_MONITORED_SERVICES, [])
        )
    async_add_entities(entities)


class PiManagerActionButton(ButtonEntity):
    def __init__(self, runtime: Any, key: str, name: str, action: Callable[[Any], Awaitable[None]]) -> None:
        self._runtime = runtime
        self._action = action
        self._attr_name = name
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_button_{key}"
        self._attr_device_info = device_info(runtime)

    async def async_press(self) -> None:
        await self._action(self._runtime)


class PiManagerServiceRestartButton(ButtonEntity):
    def __init__(self, runtime: Any, service_name: str) -> None:
        self._runtime = runtime
        self._service_name = service_name
        self._attr_name = f"Restart {service_name.removesuffix('.service')}"
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_button_restart_{slug(service_name)}"
        self._attr_device_info = device_info(runtime)

    async def async_press(self) -> None:
        await actions.async_restart_service(self._runtime, self._service_name)
