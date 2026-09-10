"""Connectivity, reboot, and monitored-service binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MONITORED_SERVICES, CONF_TEMPERATURE_WARNING, TAILSCALE_SERVICE
from .device import device_info, slug


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        PiManagerOnlineBinarySensor(runtime),
        PiManagerRebootBinarySensor(runtime),
        PiManagerTemperatureBinarySensor(runtime),
        PiManagerTailscaleBinarySensor(runtime),
    ]
    services = tuple(runtime.options.get(CONF_MONITORED_SERVICES, []))
    entities.extend(PiManagerServiceBinarySensor(runtime, name) for name in services)
    async_add_entities(entities)


class PiManagerOnlineBinarySensor(CoordinatorEntity[Any], BinarySensorEntity):
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_online"
        self._attr_device_info = device_info(runtime)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success and self.coordinator.data is not None)


class PiManagerRebootBinarySensor(CoordinatorEntity[Any], BinarySensorEntity):
    _attr_name = "Reboot required"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_reboot_required"
        self._attr_device_info = device_info(runtime)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.updates.reboot_required if self.coordinator.data else None


class PiManagerTemperatureBinarySensor(CoordinatorEntity[Any], BinarySensorEntity):
    """Expose the configured temperature warning threshold as a problem state."""

    _attr_name = "Temperature warning"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._threshold = float(runtime.options.get(CONF_TEMPERATURE_WARNING, 75.0))
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_temperature_warning"
        self._attr_device_info = device_info(runtime)

    @property
    def is_on(self) -> bool | None:
        temperature = self.coordinator.data.cpu.temperature_c if self.coordinator.data else None
        return temperature is not None and temperature >= self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        return {"warning_threshold_c": self._threshold}


class PiManagerServiceBinarySensor(CoordinatorEntity[Any], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime: Any, service_name: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._service_name = service_name
        self._attr_name = service_name.removesuffix(".service")
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_service_{slug(service_name)}"
        self._attr_device_info = device_info(runtime)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        item = next(
            (item for item in self.coordinator.data.services if item.name == self._service_name),
            None,
        )
        return item.active if item else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.coordinator.data is None:
            return None
        item = next(
            (item for item in self.coordinator.data.services if item.name == self._service_name),
            None,
        )
        return {"state": item.state, "substate": item.substate} if item else None


class PiManagerTailscaleBinarySensor(CoordinatorEntity[Any], BinarySensorEntity):
    """Expose the fixed, read-only Tailscale system service state."""

    _attr_name = "Tailscale service"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime: Any) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_tailscale_service"
        self._attr_device_info = device_info(runtime)

    def _service(self) -> Any | None:
        if not self.coordinator.data or not self.coordinator.last_update_success:
            return None
        return next(
            (item for item in self.coordinator.data.services if item.name == TAILSCALE_SERVICE),
            None,
        )

    @property
    def available(self) -> bool:
        return self._service() is not None

    @property
    def is_on(self) -> bool | None:
        item = self._service()
        return item.active if item else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        item = self._service()
        return {"state": item.state, "substate": item.substate} if item else None
