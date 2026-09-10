"""Coordinator-backed Pi Manager sensors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device import device_info, slug
from .models import HostStatus, PackagePreviewInfo


def _value(path: str) -> Callable[[HostStatus], Any]:
    parts = path.split(".")

    def getter(status: HostStatus) -> Any:
        current: Any = status
        for part in parts:
            current = getattr(current, part)
        return current

    return getter


STATIC_DESCRIPTIONS: tuple[tuple[SensorEntityDescription, Callable[[HostStatus], Any]], ...] = (
    (
        SensorEntityDescription(
            key="cpu_usage",
            name="CPU usage",
            native_unit_of_measurement=PERCENTAGE,
        ),
        _value("cpu.usage_percent"),
    ),
    (
        SensorEntityDescription(
            key="cpu_temperature",
            name="CPU temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
        ),
        _value("cpu.temperature_c"),
    ),
    (
        SensorEntityDescription(key="load_1", name="Load 1 minute"),
        _value("cpu.load_1"),
    ),
    (
        SensorEntityDescription(key="load_5", name="Load 5 minutes"),
        _value("cpu.load_5"),
    ),
    (
        SensorEntityDescription(key="load_15", name="Load 15 minutes"),
        _value("cpu.load_15"),
    ),
    (
        SensorEntityDescription(
            key="memory_usage",
            name="Memory usage",
            native_unit_of_measurement=PERCENTAGE,
        ),
        _value("memory.used_percent"),
    ),
    (
        SensorEntityDescription(
            key="memory_used",
            name="Memory used",
            native_unit_of_measurement=UnitOfInformation.BYTES,
            device_class=SensorDeviceClass.DATA_SIZE,
        ),
        _value("memory.used_bytes"),
    ),
    (
        SensorEntityDescription(
            key="uptime",
            name="Uptime",
            native_unit_of_measurement=UnitOfTime.SECONDS,
            device_class=SensorDeviceClass.DURATION,
        ),
        _value("machine.uptime_seconds"),
    ),
    (
        SensorEntityDescription(key="updates_available", name="Updates available"),
        _value("updates.available"),
    ),
    (
        SensorEntityDescription(key="security_updates", name="Security updates"),
        _value("updates.security"),
    ),
    (
        SensorEntityDescription(key="helper_version", name="Helper version"),
        _value("agent_version"),
    ),
)


MAINTENANCE_DESCRIPTIONS: tuple[tuple[str, str, Callable[[HostStatus], Any]], ...] = (
    ("package_job_state", "Package job state", lambda status: status.job.state if status.job else "idle"),
    (
        "package_job_summary",
        "Package job summary",
        lambda status: status.job.summary if status.job and status.job.summary else "No package job recorded",
    ),
    (
        "package_audit",
        "Package database",
        lambda status: (
            "Not checked"
            if status.maintenance.audit is None
            else "Healthy"
            if status.maintenance.audit.healthy
            else "Attention"
        ),
    ),
    (
        "held_packages",
        "Held packages",
        lambda status: len(status.maintenance.holds.packages) if status.maintenance.holds else 0,
    ),
    (
        "failed_service_count",
        "Failed services",
        lambda status: len(status.maintenance.failed_services.services) if status.maintenance.failed_services else 0,
    ),
)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    entities: list[SensorEntity] = [
        PiManagerValueSensor(runtime, description, getter) for description, getter in STATIC_DESCRIPTIONS
    ]
    entities.extend(
        PiManagerMaintenanceSensor(runtime, key, name, getter) for key, name, getter in MAINTENANCE_DESCRIPTIONS
    )
    entities.extend(
        PiManagerPreviewSensor(runtime, operation, name)
        for operation, name in (
            ("preview-upgrade", "Normal upgrade preview"),
            ("preview-dist-upgrade", "Dependency upgrade preview"),
            ("preview-autoremove", "Autoremove preview"),
        )
    )
    known_filesystems: set[str] = set()
    known_interfaces: set[str] = set()
    if coordinator.data is not None:
        entities.extend(_filesystem_entities(runtime, coordinator.data, known_filesystems))
        entities.extend(_network_entities(runtime, coordinator.data, known_interfaces))
    async_add_entities(entities)

    def add_dynamic() -> None:
        status = coordinator.data
        if status is None:
            return
        new_entities: list[SensorEntity] = []
        new_entities.extend(_filesystem_entities(runtime, status, known_filesystems))
        new_entities.extend(_network_entities(runtime, status, known_interfaces))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_dynamic))


class PiManagerValueSensor(CoordinatorEntity[Any], SensorEntity):
    def __init__(
        self,
        runtime: Any,
        description: SensorEntityDescription,
        getter: Callable[[HostStatus], Any],
    ) -> None:
        super().__init__(runtime.coordinator)
        self.entity_description = description
        self._runtime = runtime
        self._getter = getter
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_{description.key}"
        self._attr_device_info = device_info(runtime)

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self._getter(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        machine = self.coordinator.data.machine
        return {
            "hostname": machine.hostname,
            "model": machine.model,
            "os": machine.os,
            "architecture": machine.arch,
        }


class PiManagerMaintenanceSensor(CoordinatorEntity[Any], SensorEntity):
    """Expose retained package maintenance results without a second SSH call."""

    def __init__(self, runtime: Any, key: str, name: str, getter: Callable[[HostStatus], Any]) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._getter = getter
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_{key}"
        self._attr_device_info = device_info(runtime)

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self._getter(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self.coordinator.data
        if status is None:
            return None
        if self._key == "package_job_state" or self._key == "package_job_summary":
            job = status.job
            return (
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "state": job.state,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "exit_code": job.exit_code,
                }
                if job
                else {"job_id": None, "job_type": None}
            )
        if self._key == "package_audit":
            audit = status.maintenance.audit
            return (
                {
                    "last_checked": audit.last_checked,
                    "healthy": audit.healthy,
                    "dpkg_issues": audit.dpkg_issues,
                    "apt_healthy": audit.apt_healthy,
                }
                if audit
                else {"last_checked": None}
            )
        if self._key == "held_packages":
            holds = status.maintenance.holds
            return (
                {"last_checked": holds.last_checked, "packages": list(holds.packages)}
                if holds
                else {"last_checked": None}
            )
        failed = status.maintenance.failed_services
        return (
            {"last_checked": failed.last_checked, "services": list(failed.services)}
            if failed
            else {"last_checked": None}
        )


class PiManagerPreviewSensor(CoordinatorEntity[Any], SensorEntity):
    def __init__(self, runtime: Any, operation: str, name: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._operation = operation
        self._attr_name = name
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_{operation.replace('-', '_')}"
        self._attr_device_info = device_info(runtime)

    @property
    def native_value(self) -> str:
        preview = self._preview()
        return preview.summary if preview and preview.summary else "Not checked"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        preview = self._preview()
        if preview is None:
            return {"last_checked": None}
        return {
            "last_checked": preview.last_checked,
            "upgraded": preview.upgraded,
            "newly_installed": preview.newly_installed,
            "to_remove": preview.to_remove,
            "not_upgraded": preview.not_upgraded,
            "reboot_required": preview.reboot_required,
        }

    def _preview(self) -> PackagePreviewInfo | None:
        status = self.coordinator.data
        if status is None:
            return None
        return next(
            (preview for preview in status.maintenance.previews if preview.operation == self._operation),
            None,
        )


class PiManagerFilesystemSensor(CoordinatorEntity[Any], SensorEntity):
    def __init__(self, runtime: Any, mount: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._mount = mount
        self._attr_name = f"{mount} usage"
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_filesystem_{slug(mount)}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_info = device_info(runtime)

    @property
    def native_value(self) -> float | None:
        item = self._item()
        return item.used_percent if item else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        item = self._item()
        if item is None:
            return None
        return {
            "mount": item.mount,
            "device": item.device,
            "filesystem_type": item.fstype,
            "total_bytes": item.total_bytes,
            "used_bytes": item.used_bytes,
            "readonly": item.readonly,
        }

    def _item(self) -> Any:
        if self.coordinator.data is None:
            return None
        return next((item for item in self.coordinator.data.filesystems if item.mount == self._mount), None)


class PiManagerNetworkSensor(CoordinatorEntity[Any], SensorEntity):
    def __init__(self, runtime: Any, interface: str, direction: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._interface = interface
        self._direction = direction
        self._attr_name = f"{interface} {direction} bytes"
        self._attr_unique_id = f"{runtime.entry.data['machine_id']}_network_{slug(interface)}_{direction}"
        self._attr_native_unit_of_measurement = UnitOfInformation.BYTES
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_device_info = device_info(runtime)

    @property
    def native_value(self) -> int | None:
        item = self._item()
        return getattr(item, f"{self._direction}_bytes") if item else None

    def _item(self) -> Any:
        if self.coordinator.data is None:
            return None
        return next(
            (item for item in self.coordinator.data.network if item.interface == self._interface),
            None,
        )


def _filesystem_entities(runtime: Any, status: HostStatus, known: set[str]) -> list[SensorEntity]:
    result: list[SensorEntity] = []
    for item in status.filesystems:
        if item.mount not in known:
            known.add(item.mount)
            result.append(PiManagerFilesystemSensor(runtime, item.mount))
    return result


def _network_entities(runtime: Any, status: HostStatus, known: set[str]) -> list[SensorEntity]:
    result: list[SensorEntity] = []
    for item in status.network:
        if item.interface not in known:
            known.add(item.interface)
            result.extend(
                [
                    PiManagerNetworkSensor(runtime, item.interface, "rx"),
                    PiManagerNetworkSensor(runtime, item.interface, "tx"),
                ]
            )
    return result
