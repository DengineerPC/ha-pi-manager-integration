from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.pi_manager.binary_sensor import (
    PiManagerOnlineBinarySensor,
    PiManagerTailscaleBinarySensor,
)
from custom_components.pi_manager.contract import parse_status
from custom_components.pi_manager.models import ServiceInfo
from custom_components.pi_manager.sensor import STATIC_DESCRIPTIONS, PiManagerFilesystemSensor, PiManagerValueSensor


def _runtime(machine_id: str, host: str):
    fixture = Path(__file__).parent / "fixtures" / "healthy_status.json"
    status = parse_status(json.loads(fixture.read_text(encoding="utf-8")))
    entry = SimpleNamespace(
        data={"machine_id": machine_id, "host": host, "display_name": "Pi host", "helper_version": "0.1.0"},
        options={},
    )
    coordinator = SimpleNamespace(data=status, last_update_success=True)
    return SimpleNamespace(entry=entry, coordinator=coordinator, options={})


def test_entity_ids_use_machine_identity_not_host() -> None:
    first = _runtime("a" * 32, "pi-a.example")
    moved = _runtime("a" * 32, "192.0.2.44")
    description, getter = STATIC_DESCRIPTIONS[0]

    first_sensor = PiManagerValueSensor(first, description, getter)
    moved_sensor = PiManagerValueSensor(moved, description, getter)
    assert first_sensor.unique_id == moved_sensor.unique_id == f"{'a' * 32}_cpu_usage"

    filesystem = PiManagerFilesystemSensor(first, "/mnt/extstorage")
    online = PiManagerOnlineBinarySensor(first)
    assert filesystem.unique_id == f"{'a' * 32}_filesystem_mnt_extstorage"
    assert online.unique_id == f"{'a' * 32}_online"
    assert first_sensor.native_value == 12.4


def test_tailscale_sensor_is_unavailable_when_service_is_absent() -> None:
    runtime = _runtime("a" * 32, "pi-a.example")
    sensor = PiManagerTailscaleBinarySensor(runtime)

    assert sensor.unique_id == f"{'a' * 32}_tailscale_service"
    assert sensor.available is False
    assert sensor.is_on is None


@pytest.mark.parametrize(
    ("active", "state", "substate"),
    ((True, "active", "running"), (False, "inactive", "dead")),
)
def test_tailscale_sensor_reflects_service_state(active: bool, state: str, substate: str) -> None:
    runtime = _runtime("a" * 32, "pi-a.example")
    runtime.coordinator.data = replace(
        runtime.coordinator.data,
        services=(ServiceInfo("tailscaled.service", active, state, substate),),
    )
    sensor = PiManagerTailscaleBinarySensor(runtime)

    assert sensor.available is True
    assert sensor.is_on is active
    assert sensor.extra_state_attributes == {"state": state, "substate": substate}
