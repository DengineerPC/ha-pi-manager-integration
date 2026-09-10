"""Repair issue helpers for trust and helper compatibility failures."""

from __future__ import annotations

import logging
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_create_fingerprint_issue(hass: Any, entry_id: str) -> None:
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
    except ImportError, AttributeError:
        _LOGGER.debug("Home Assistant repair issue API unavailable")


async def async_create_helper_issue(hass: Any, entry_id: str) -> None:
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
    except ImportError, AttributeError:
        _LOGGER.debug("Home Assistant repair issue API unavailable")
