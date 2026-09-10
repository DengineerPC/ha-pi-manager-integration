"""Home Assistant entry point for Pi Manager."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DEFAULT_OPTIONS, DOMAIN, PLATFORMS
from .coordinator import PiManagerCoordinator
from .runtime import PiManagerRuntime

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration namespace; normal setup is config-entry based."""

    del config
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one independently isolated managed host."""

    options = {**DEFAULT_OPTIONS, **entry.options}
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)
    runtime = PiManagerRuntime(hass=hass, entry=entry, key_store=_key_store(hass))
    coordinator = PiManagerCoordinator(hass, runtime)
    runtime.coordinator = coordinator
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    refresh_task = hass.async_create_task(coordinator.async_refresh(), name=f"pi_manager_refresh_{entry.entry_id}")
    entry.async_on_unload(refresh_task.cancel)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: PiManagerRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if runtime is not None:
        await runtime.async_close()
    return bool(unload_ok)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removing an entry leaves unrelated remote SSH configuration intact."""

    del hass, entry


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _key_store(hass: HomeAssistant) -> Any:
    from .key_store import KeyStore

    return KeyStore(hass.config.config_dir)
