"""Secret-safe Home Assistant diagnostics."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from .const import (
    CONF_FINGERPRINT,
    CONF_KEY_ID,
    CONF_PASSWORD,
    DOMAIN,
    INTEGRATION_VERSION,
)
from .models import HostStatus

TO_REDACT = {
    CONF_PASSWORD,
    CONF_KEY_ID,
    CONF_FINGERPRINT,
    "private_key",
    "authorized_keys",
    "environment",
}


async def async_get_config_entry_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """Return bounded non-secret entry/runtime data."""

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    status = runtime.coordinator.data if runtime is not None else None
    status_data = asdict(cast(HostStatus, status)) if status is not None else None
    if status_data and isinstance(status_data.get("job"), dict):
        summary = status_data["job"].get("summary")
        status_data["job"]["summary"] = str(summary)[:256] if summary is not None else None
    entry_data = {key: ("<redacted>" if key in TO_REDACT else value) for key, value in entry.data.items()}
    return {
        "integration_version": INTEGRATION_VERSION,
        "entry_data": entry_data,
        "options": dict(entry.options),
        "last_update_success": bool(runtime and runtime.coordinator.last_update_success),
        "last_error_category": getattr(runtime.coordinator, "last_error_category", None) if runtime else None,
        "status": status_data,
    }
