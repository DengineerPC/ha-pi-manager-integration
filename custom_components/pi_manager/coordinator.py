"""Single bounded status refresh for one Pi Manager host."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN
from .errors import HelperIncompatibleError, HostFingerprintMismatch, PiManagerConnectionError, PiManagerError
from .models import HostStatus
from .upgrade import async_upgrade_if_needed

_LOGGER = logging.getLogger(__name__)


class PiManagerCoordinator(DataUpdateCoordinator[HostStatus]):
    """Fetch one complete status document per polling interval."""

    def __init__(self, hass: Any, runtime: Any) -> None:
        interval = int(runtime.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=runtime.entry,
            name=f"{DOMAIN}_{runtime.entry.entry_id}",
            update_interval=timedelta(seconds=interval),
        )
        self.runtime = runtime
        self.last_error_category: str | None = None

    async def _async_update_data(self) -> HostStatus:
        try:
            status = await self.runtime.async_status()
            try:
                package_update = await self.runtime.async_check_updates_if_due()
            except PiManagerError as err:
                _LOGGER.debug(
                    "Pi Manager package check unavailable for %s: %s",
                    self.runtime.entry.entry_id,
                    err.__class__.__name__,
                )
            else:
                if package_update is not None:
                    status = replace(
                        status,
                        updates=replace(
                            status.updates,
                            last_checked=package_update.last_checked,
                            available=package_update.available,
                            security=package_update.security,
                        ),
                    )
            status = await async_upgrade_if_needed(self.runtime, status)
        except HostFingerprintMismatch as err:
            self.last_error_category = "fingerprint_changed"
            _LOGGER.error(
                "Pi Manager SSH host fingerprint changed for config entry %s",
                self.runtime.entry.entry_id,
            )
            _create_fingerprint_issue(self.hass, self.runtime.entry.entry_id)
            raise UpdateFailed("SSH host fingerprint changed") from err
        except HelperIncompatibleError as err:
            self.last_error_category = "helper_incompatible"
            _create_helper_issue(self.hass, self.runtime.entry.entry_id)
            raise UpdateFailed("Helper upgrade requires attention") from err
        except PiManagerError as err:
            self.last_error_category = _category_from_error(err)
            if self.last_error_category not in {"fingerprint_changed"}:
                _LOGGER.debug(
                    "Pi Manager refresh unavailable for %s: %s",
                    self.runtime.entry.entry_id,
                    self.last_error_category,
                )
            raise UpdateFailed(self.last_error_category) from err
        self.last_error_category = None
        return status


def _category_from_error(error: PiManagerError) -> str:
    if isinstance(error, PiManagerConnectionError):
        return "cannot_connect"
    text = str(error)
    if text.startswith("helper_"):
        return text
    return error.__class__.__name__.replace("PiManager", "").lower() or "refresh_failed"


def _create_fingerprint_issue(hass: Any, entry_id: str) -> None:
    try:
        from homeassistant.helpers import issue_registry as ir

        ir.async_create_issue(
            hass,
            DOMAIN,
            f"fingerprint_{entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="fingerprint_changed",
            translation_placeholders={"entry_id": entry_id},
        )
    except (ImportError, AttributeError):
        _LOGGER.debug("Repair issue API unavailable while reporting fingerprint mismatch")


def _create_helper_issue(hass: Any, entry_id: str) -> None:
    try:
        from homeassistant.helpers import issue_registry as ir

        ir.async_create_issue(
            hass,
            DOMAIN,
            f"helper_{entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="helper_incompatible",
            translation_placeholders={"entry_id": entry_id},
        )
    except (ImportError, AttributeError):
        _LOGGER.debug("Repair issue API unavailable while reporting helper mismatch")
