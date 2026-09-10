"""Shared Home Assistant device/entity helpers."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def machine_identifier(machine_id: str) -> tuple[str, str]:
    return (DOMAIN, machine_id)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unknown"


def device_info(runtime: Any, *, model: str | None = None, agent_version: str | None = None) -> DeviceInfo:
    data = runtime.entry.data
    status = runtime.coordinator.data
    machine = status.machine if status is not None else None
    return DeviceInfo(
        identifiers={machine_identifier(data["machine_id"])},
        name=data.get("display_name") or (machine.hostname if machine else data["host"]),
        manufacturer="Pi Manager",
        model=model or (machine.model if machine else "Raspberry Pi / Debian host"),
        sw_version=agent_version or (status.agent_version if status else data.get("helper_version")),
        serial_number=data["machine_id"],
    )
