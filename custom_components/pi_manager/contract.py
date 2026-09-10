"""Parse and validate the versioned remote helper contract."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .const import MAX_COMMAND_OUTPUT, SCHEMA_VERSION
from .errors import HelperProtocolError
from .models import (
    CpuInfo,
    FailedServicesInfo,
    FilesystemInfo,
    HostStatus,
    JobInfo,
    MachineInfo,
    MaintenanceInfo,
    MemoryInfo,
    NetworkInfo,
    PackageAuditInfo,
    PackageHoldsInfo,
    PackagePreviewInfo,
    ServiceInfo,
    UpdateInfo,
)

_MACHINE_ID = re.compile(r"^[a-fA-F0-9]{16,128}$")


def parse_json_response(stdout: str) -> Mapping[str, Any]:
    """Decode a bounded helper response object."""

    if len(stdout.encode("utf-8", errors="replace")) > MAX_COMMAND_OUTPUT:
        raise HelperProtocolError("helper_response_too_large")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as err:
        raise HelperProtocolError("helper_invalid_json") from err
    if not isinstance(payload, dict):
        raise HelperProtocolError("helper_response_not_object")
    return payload


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HelperProtocolError(f"helper_field_not_object:{field}")
    return value


def _string(value: Any, field: str, *, default: str = "") -> str:
    if value is None and default:
        return default
    if not isinstance(value, str):
        raise HelperProtocolError(f"helper_field_not_string:{field}")
    if len(value) > 4096:
        raise HelperProtocolError(f"helper_field_too_large:{field}")
    return value


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _number(value: Any, field: str, *, integer: bool = False, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HelperProtocolError(f"helper_field_not_number:{field}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise HelperProtocolError(f"helper_field_invalid_number:{field}")
    return int(number) if integer else number


def _bool(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise HelperProtocolError(f"helper_field_not_bool:{field}")
    return value


def parse_status(payload: Mapping[str, Any]) -> HostStatus:
    """Convert a validated JSON mapping into immutable runtime models."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise HelperProtocolError("helper_schema_unsupported")

    machine_data = _mapping(payload.get("machine"), "machine")
    cpu_data = _mapping(payload.get("cpu"), "cpu")
    memory_data = _mapping(payload.get("memory"), "memory")
    updates_data = _mapping(payload.get("updates"), "updates")

    machine_id = _string(machine_data.get("machine_id"), "machine.machine_id")
    if not _MACHINE_ID.fullmatch(machine_id):
        raise HelperProtocolError("helper_machine_id_invalid")
    machine = MachineInfo(
        machine_id=machine_id,
        hostname=_string(machine_data.get("hostname"), "machine.hostname"),
        model=_string(machine_data.get("model"), "machine.model", default="Unknown"),
        os=_string(machine_data.get("os"), "machine.os", default="Unknown"),
        kernel=_string(machine_data.get("kernel"), "machine.kernel", default="Unknown"),
        arch=_string(machine_data.get("arch"), "machine.arch", default="Unknown"),
        uptime_seconds=_number(machine_data.get("uptime_seconds"), "machine.uptime_seconds", default=0.0),
    )
    cpu = CpuInfo(
        usage_percent=_number(cpu_data.get("usage_percent"), "cpu.usage_percent", default=None),
        temperature_c=_number(cpu_data.get("temperature_c"), "cpu.temperature_c", default=None),
        load_1=_number(cpu_data.get("load_1"), "cpu.load_1", default=None),
        load_5=_number(cpu_data.get("load_5"), "cpu.load_5", default=None),
        load_15=_number(cpu_data.get("load_15"), "cpu.load_15", default=None),
    )
    memory = MemoryInfo(
        total_bytes=_number(memory_data.get("total_bytes"), "memory.total_bytes", integer=True, default=0),
        used_bytes=_number(memory_data.get("used_bytes"), "memory.used_bytes", integer=True, default=0),
        used_percent=_number(memory_data.get("used_percent"), "memory.used_percent", default=0.0),
    )

    filesystems_value = payload.get("filesystems", [])
    if not isinstance(filesystems_value, list) or len(filesystems_value) > 512:
        raise HelperProtocolError("helper_filesystems_invalid")
    filesystems = tuple(_parse_filesystem(item, index) for index, item in enumerate(filesystems_value))

    network_value = payload.get("network", [])
    if not isinstance(network_value, list) or len(network_value) > 256:
        raise HelperProtocolError("helper_network_invalid")
    network = tuple(_parse_network(item, index) for index, item in enumerate(network_value))

    services_value = payload.get("services", [])
    if not isinstance(services_value, list) or len(services_value) > 256:
        raise HelperProtocolError("helper_services_invalid")
    services = tuple(_parse_service(item, index) for index, item in enumerate(services_value))

    job_value = payload.get("job")
    job = None if job_value is None else _parse_job(job_value)
    maintenance = _parse_maintenance(payload.get("maintenance"))
    return HostStatus(
        schema_version=SCHEMA_VERSION,
        agent_version=_string(payload.get("agent_version"), "agent_version", default="unknown"),
        machine=machine,
        cpu=cpu,
        memory=memory,
        filesystems=filesystems,
        network=network,
        updates=_parse_update_info(updates_data, include_reboot=True),
        services=services,
        job=job,
        maintenance=maintenance,
    )


def parse_update_response(payload: Mapping[str, Any]) -> UpdateInfo:
    """Parse the bounded response returned by ``check-updates --json``."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise HelperProtocolError("helper_schema_unsupported")
    return _parse_update_info(_mapping(payload.get("updates"), "updates"), include_reboot=False)


def _parse_update_info(value: Mapping[str, Any], *, include_reboot: bool) -> UpdateInfo:
    return UpdateInfo(
        last_checked=_optional_string(value.get("last_checked"), "updates.last_checked"),
        available=_number(value.get("available"), "updates.available", integer=True, default=0),
        security=_number(value.get("security"), "updates.security", integer=True, default=0),
        reboot_required=_bool(value.get("reboot_required"), "updates.reboot_required") if include_reboot else False,
    )


def _parse_filesystem(value: Any, index: int) -> FilesystemInfo:
    item = _mapping(value, f"filesystems[{index}]")
    return FilesystemInfo(
        mount=_string(item.get("mount"), f"filesystems[{index}].mount"),
        device=_string(item.get("device"), f"filesystems[{index}].device", default="unknown"),
        fstype=_string(item.get("fstype"), f"filesystems[{index}].fstype", default="unknown"),
        total_bytes=_number(item.get("total_bytes"), f"filesystems[{index}].total_bytes", integer=True, default=0),
        used_bytes=_number(item.get("used_bytes"), f"filesystems[{index}].used_bytes", integer=True, default=0),
        used_percent=_number(item.get("used_percent"), f"filesystems[{index}].used_percent", default=0.0),
        readonly=_bool(item.get("readonly"), f"filesystems[{index}].readonly"),
    )


def _parse_network(value: Any, index: int) -> NetworkInfo:
    item = _mapping(value, f"network[{index}]")
    return NetworkInfo(
        interface=_string(item.get("interface"), f"network[{index}].interface"),
        rx_bytes=_number(item.get("rx_bytes"), f"network[{index}].rx_bytes", integer=True, default=0),
        tx_bytes=_number(item.get("tx_bytes"), f"network[{index}].tx_bytes", integer=True, default=0),
        operstate=_string(item.get("operstate"), f"network[{index}].operstate", default="unknown"),
    )


def _parse_service(value: Any, index: int) -> ServiceInfo:
    item = _mapping(value, f"services[{index}]")
    return ServiceInfo(
        name=_string(item.get("name"), f"services[{index}].name"),
        active=_bool(item.get("active"), f"services[{index}].active"),
        state=_string(item.get("state"), f"services[{index}].state", default="unknown"),
        substate=_string(item.get("substate"), f"services[{index}].substate", default="unknown"),
    )


def _parse_job(value: Any) -> JobInfo:
    item = _mapping(value, "job")
    return JobInfo(
        job_id=_string(item.get("id"), "job.id"),
        job_type=_string(item.get("type"), "job.type"),
        state=_string(item.get("state"), "job.state"),
        started_at=_optional_string(item.get("started_at"), "job.started_at"),
        finished_at=_optional_string(item.get("finished_at"), "job.finished_at"),
        exit_code=_number(item.get("exit_code"), "job.exit_code", integer=True, default=None),
        summary=_optional_string(item.get("summary"), "job.summary"),
    )


def _parse_maintenance(value: Any) -> MaintenanceInfo:
    """Parse optional maintenance state while keeping old helpers compatible."""

    if value is None:
        return MaintenanceInfo()
    item = _mapping(value, "maintenance")
    previews_value = item.get("previews", [])
    if not isinstance(previews_value, list) or len(previews_value) > 8:
        raise HelperProtocolError("helper_maintenance_previews_invalid")
    previews = tuple(_parse_preview(preview, index) for index, preview in enumerate(previews_value))

    audit_value = item.get("audit")
    audit = None if audit_value is None else _parse_audit(audit_value)
    holds_value = item.get("holds")
    holds = None if holds_value is None else _parse_holds(holds_value)
    failed_value = item.get("failed_services")
    failed_services = None if failed_value is None else _parse_failed_services(failed_value)
    return MaintenanceInfo(
        previews=previews,
        audit=audit,
        holds=holds,
        failed_services=failed_services,
    )


def _parse_preview(value: Any, index: int) -> PackagePreviewInfo:
    item = _mapping(value, f"maintenance.previews[{index}]")
    return PackagePreviewInfo(
        operation=_string(item.get("operation"), f"maintenance.previews[{index}].operation"),
        last_checked=_optional_string(item.get("last_checked"), f"maintenance.previews[{index}].last_checked"),
        upgraded=_number(item.get("upgraded"), f"maintenance.previews[{index}].upgraded", integer=True, default=0),
        newly_installed=_number(
            item.get("newly_installed"),
            f"maintenance.previews[{index}].newly_installed",
            integer=True,
            default=0,
        ),
        to_remove=_number(item.get("to_remove"), f"maintenance.previews[{index}].to_remove", integer=True, default=0),
        not_upgraded=_number(
            item.get("not_upgraded"),
            f"maintenance.previews[{index}].not_upgraded",
            integer=True,
            default=0,
        ),
        reboot_required=_bool(item.get("reboot_required"), f"maintenance.previews[{index}].reboot_required"),
        summary=_optional_string(item.get("summary"), f"maintenance.previews[{index}].summary"),
    )


def _parse_audit(value: Any) -> PackageAuditInfo:
    item = _mapping(value, "maintenance.audit")
    return PackageAuditInfo(
        last_checked=_optional_string(item.get("last_checked"), "maintenance.audit.last_checked"),
        healthy=_bool(item.get("healthy"), "maintenance.audit.healthy"),
        dpkg_issues=_number(item.get("dpkg_issues"), "maintenance.audit.dpkg_issues", integer=True, default=0),
        apt_healthy=_bool(item.get("apt_healthy"), "maintenance.audit.apt_healthy"),
        summary=_optional_string(item.get("summary"), "maintenance.audit.summary"),
    )


def _parse_holds(value: Any) -> PackageHoldsInfo:
    item = _mapping(value, "maintenance.holds")
    packages = item.get("packages", [])
    if not isinstance(packages, list) or len(packages) > 256:
        raise HelperProtocolError("helper_maintenance_holds_invalid")
    return PackageHoldsInfo(
        last_checked=_optional_string(item.get("last_checked"), "maintenance.holds.last_checked"),
        packages=tuple(
            _string(package, f"maintenance.holds.packages[{index}]") for index, package in enumerate(packages)
        ),
    )


def _parse_failed_services(value: Any) -> FailedServicesInfo:
    item = _mapping(value, "maintenance.failed_services")
    services = item.get("services", [])
    if not isinstance(services, list) or len(services) > 256:
        raise HelperProtocolError("helper_maintenance_failed_services_invalid")
    return FailedServicesInfo(
        last_checked=_optional_string(item.get("last_checked"), "maintenance.failed_services.last_checked"),
        services=tuple(
            _string(service, f"maintenance.failed_services.services[{index}]") for index, service in enumerate(services)
        ),
    )
