"""Typed models for the versioned Pi Manager JSON contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MachineInfo:
    machine_id: str
    hostname: str
    model: str
    os: str
    kernel: str
    arch: str
    uptime_seconds: float


@dataclass(frozen=True, slots=True)
class CpuInfo:
    usage_percent: float | None
    temperature_c: float | None
    load_1: float | None
    load_5: float | None
    load_15: float | None


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    total_bytes: int
    used_bytes: int
    used_percent: float


@dataclass(frozen=True, slots=True)
class FilesystemInfo:
    mount: str
    device: str
    fstype: str
    total_bytes: int
    used_bytes: int
    used_percent: float
    readonly: bool


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    interface: str
    rx_bytes: int
    tx_bytes: int
    operstate: str


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    last_checked: str | None
    available: int
    security: int
    reboot_required: bool


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    name: str
    active: bool
    state: str
    substate: str


@dataclass(frozen=True, slots=True)
class JobInfo:
    job_id: str
    job_type: str
    state: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    summary: str | None


@dataclass(frozen=True, slots=True)
class PackagePreviewInfo:
    operation: str
    last_checked: str | None
    upgraded: int
    newly_installed: int
    to_remove: int
    not_upgraded: int
    reboot_required: bool
    summary: str | None


@dataclass(frozen=True, slots=True)
class PackageAuditInfo:
    last_checked: str | None
    healthy: bool
    dpkg_issues: int
    apt_healthy: bool
    summary: str | None


@dataclass(frozen=True, slots=True)
class PackageHoldsInfo:
    last_checked: str | None
    packages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FailedServicesInfo:
    last_checked: str | None
    services: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaintenanceInfo:
    previews: tuple[PackagePreviewInfo, ...] = ()
    audit: PackageAuditInfo | None = None
    holds: PackageHoldsInfo | None = None
    failed_services: FailedServicesInfo | None = None


@dataclass(frozen=True, slots=True)
class HostStatus:
    schema_version: int
    agent_version: str
    machine: MachineInfo
    cpu: CpuInfo
    memory: MemoryInfo
    filesystems: tuple[FilesystemInfo, ...]
    network: tuple[NetworkInfo, ...]
    updates: UpdateInfo
    services: tuple[ServiceInfo, ...]
    job: JobInfo | None
    maintenance: MaintenanceInfo = MaintenanceInfo()


@dataclass(frozen=True, slots=True)
class HostDiscovery:
    machine_id: str
    hostname: str
    os_name: str
    os_version: str
    architecture: str
    python_version: str
    systemd_version: str
    apt_version: str
    sudo_available: bool
    helper_installed: bool
    helper_version: str | None
    target_writable: bool
    fingerprint: str
    raw_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True, slots=True)
class KeyMaterial:
    key_id: str
    private_key: str
    public_key: str
    upgrade_secret: str = ""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    machine_id: str
    key_id: str
    fingerprint: str
    helper_version: str
    changed_files: tuple[str, ...]


def as_number(value: Any, *, default: float | None = None) -> float | None:
    """Return a finite float or a default."""

    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) != float("inf") else default
