#!/usr/bin/env python3
"""Root-owned, bounded Pi Manager host helper.

This module intentionally exposes only explicit operations. It never accepts a
shell command, arbitrary apt arguments, arbitrary systemd units, or a generic
subprocess request. All machine-readable output is JSON with a schema version;
diagnostic detail is kept on stderr and bounded.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_fcntl: Any
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - only used on non-POSIX development hosts
    _fcntl = None

SCHEMA_VERSION = 1
AGENT_VERSION = "0.2.5"
CONFIG_PATH = Path("/etc/pi-manager/config.json")
TRUST_PATH = Path("/etc/pi-manager/trust.json")
STATE_PATH = Path("/var/lib/pi-manager/state.json")
UPDATE_LOCK_PATH = Path("/var/lib/pi-manager/update.lock")
REMOTE_AGENT_PATH = "/usr/local/lib/pi-manager/pi_manager_agent.py"
REMOTE_CTL_PATH = "/usr/local/sbin/pi-managerctl"
REMOTE_SUDOERS_PATH = "/etc/sudoers.d/pi-manager"
TAILSCALE_SERVICE = "tailscaled.service"
MAX_JSON_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT = 256 * 1024
MAX_SUDOERS_BYTES = 64 * 1024
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]*\.service$")
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@+_.:~-]{0,255}$")
JOB_UNIT_RE = re.compile(r"^pi-manager-(?:update|job)-[a-f0-9]{32}$")
SUDOERS_POLICY_VERSION_RE = re.compile(r"^# Pi Manager sudo policy version: (\d+\.\d+\.\d+)$")
SUDOERS_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
PSEUDO_FILESYSTEMS = {
    "autofs",
    "binfmt_misc",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devpts",
    "devtmpfs",
    "efivarfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "overlay",
    "proc",
    "pstore",
    "ramfs",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}

# Keep this list in lockstep with the bundled sudoers template. It is repeated
# here intentionally: the remote helper is deployed as a standalone file and
# must validate a policy without importing Home Assistant code.
SUDOERS_POLICY_COMMANDS: tuple[str, ...] = (
    "/usr/local/sbin/pi-managerctl status --json",
    "/usr/local/sbin/pi-managerctl check-updates --json",
    "/usr/local/sbin/pi-managerctl update --json",
    "/usr/local/sbin/pi-managerctl upgrade --json",
    "/usr/local/sbin/pi-managerctl dist-upgrade --json",
    "/usr/local/sbin/pi-managerctl preview-upgrade --json",
    "/usr/local/sbin/pi-managerctl preview-dist-upgrade --json",
    "/usr/local/sbin/pi-managerctl preview-autoremove --json",
    "/usr/local/sbin/pi-managerctl audit-packages --json",
    "/usr/local/sbin/pi-managerctl show-package-holds --json",
    "/usr/local/sbin/pi-managerctl failed-services --json",
    "/usr/local/sbin/pi-managerctl configure-packages --json",
    "/usr/local/sbin/pi-managerctl repair-packages --json",
    "/usr/local/sbin/pi-managerctl autoremove --json",
    "/usr/local/sbin/pi-managerctl autoclean --json",
    "/usr/local/sbin/pi-managerctl clean-cache --json",
    "/usr/local/sbin/pi-managerctl reboot --json",
    "/usr/local/sbin/pi-managerctl shutdown --json",
    "/usr/local/sbin/pi-managerctl service-status *",
    "/usr/local/sbin/pi-managerctl service-restart *",
    "/usr/local/sbin/pi-managerctl service-validate *",
    "/usr/local/sbin/pi-managerctl configure-services --json --services-json *",
    "/usr/local/sbin/pi-managerctl configure-trust --json --secret *",
    "/usr/local/sbin/pi-managerctl upgrade-helper --json --version * --agent * --ctl * --signature *",
    "/usr/local/sbin/pi-managerctl upgrade-helper --json --version * --agent * --ctl * --signature * --sudoers *",
)

# These are deliberately fixed argument lists. Values from SSH, Home
# Assistant, or a config file are never appended to an apt or dpkg command.
PACKAGE_JOB_COMMANDS: dict[str, tuple[str, ...]] = {
    "upgrade": ("/usr/bin/apt-get", "-y", "upgrade"),
    "dist-upgrade": ("/usr/bin/apt-get", "-y", "dist-upgrade"),
    "configure-packages": ("/usr/bin/dpkg", "--configure", "-a"),
    "repair-packages": ("/usr/bin/apt-get", "-f", "-y", "install"),
    "autoremove": ("/usr/bin/apt-get", "-y", "autoremove"),
    "autoclean": ("/usr/bin/apt-get", "autoclean"),
    "clean-cache": ("/usr/bin/apt-get", "clean"),
}
PACKAGE_JOB_LABELS: dict[str, str] = {
    "upgrade": "Normal package upgrade",
    "dist-upgrade": "Dependency-changing upgrade",
    "configure-packages": "Package configuration",
    "repair-packages": "Broken dependency repair",
    "autoremove": "Unused dependency removal",
    "autoclean": "Obsolete package cleanup",
    "clean-cache": "Package cache cleanup",
}
PACKAGE_JOBS_REQUIRING_REFRESH = frozenset({"upgrade", "dist-upgrade", "repair-packages", "autoremove"})
PACKAGE_PREVIEW_COMMANDS: dict[str, tuple[str, ...]] = {
    "preview-upgrade": ("/usr/bin/apt-get", "-s", "-q", "upgrade"),
    "preview-dist-upgrade": ("/usr/bin/apt-get", "-s", "-q", "dist-upgrade"),
    "preview-autoremove": ("/usr/bin/apt-get", "-s", "-q", "autoremove"),
}


class AgentError(Exception):
    """Expected helper error represented by a stable machine code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail[:512]


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _emit(payload: Mapping[str, Any], *, returncode: int = 0) -> int:
    response = {"schema_version": SCHEMA_VERSION, **payload}
    encoded = json.dumps(response, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        response = {
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": "response_too_large",
                "message": "Response exceeded the bounded output limit",
            },
        }
    print(json.dumps(response, separators=(",", ":"), sort_keys=True))
    return returncode


def _read_json(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    # This source is formatted with the HA runtime target (Python 3.14), but
    # is executed on managed hosts with Python 3.13. Preserve the portable
    # exception spelling below.
    # fmt: off
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(default)
    # fmt: on
    return dict(data) if isinstance(data, dict) else dict(default)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    temporary.write_text(encoded, encoding="utf-8")
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


@contextmanager
def _update_lock() -> Iterator[None]:
    """Serialize helper processes while they inspect/start the update job."""

    UPDATE_LOCK_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with UPDATE_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        os.chmod(UPDATE_LOCK_PATH, 0o640)
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _run(
    args: Sequence[str],
    *,
    timeout: float = 30.0,
    check: bool = False,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stdout_file = tempfile.TemporaryFile()
    stderr_file = tempfile.TemporaryFile()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env={**os.environ, **env} if env is not None else None,
        )
        if input_text is not None and process.stdin is not None:
            try:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.close()
            except BrokenPipeError:
                pass
        returncode = process.wait(timeout=timeout)
        stdout = _read_bounded_file(stdout_file)
        stderr = _read_bounded_file(stderr_file)
        result = subprocess.CompletedProcess(list(args), returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    except subprocess.TimeoutExpired as err:
        if process is not None:
            process.kill()
            process.wait()
        raise AgentError("command_timeout") from err
    except OSError as err:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise AgentError("command_unavailable") from err
    finally:
        stdout_file.close()
        stderr_file.close()


def _read_bounded_file(handle: Any) -> str:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size > MAX_COMMAND_OUTPUT:
        raise AgentError("command_output_too_large")
    handle.seek(0)
    return handle.read(MAX_COMMAND_OUTPUT).decode("utf-8", errors="replace")


def _read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def _machine() -> dict[str, Any]:
    os_release = _parse_os_release(_read_text("/etc/os-release"))
    model = _read_text("/proc/device-tree/model", "Generic Debian host").replace("\x00", "")
    uptime_value = _read_text("/proc/uptime", "0").split()[0]
    try:
        uptime = max(0.0, float(uptime_value))
    except ValueError:
        uptime = 0.0
    uname = platform.uname()
    return {
        "machine_id": _read_text("/etc/machine-id", "unknown"),
        "hostname": uname.node,
        "model": model,
        "os": os_release.get("PRETTY_NAME", os_release.get("NAME", "Unknown")).strip('"'),
        "kernel": uname.release,
        "arch": uname.machine,
        "uptime_seconds": uptime,
    }


def _parse_os_release(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _cpu(state: Mapping[str, Any]) -> dict[str, Any]:
    line = next((line for line in _read_text("/proc/stat").splitlines() if line.startswith("cpu ")), "")
    fields = line.split()[1:]
    values = [int(value) for value in fields[:8] if value.isdigit()]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0) if len(values) > 3 else 0
    raw_previous = state.get("cpu")
    previous: Mapping[str, Any] = raw_previous if isinstance(raw_previous, Mapping) else {}
    previous_total = int(previous.get("total", total))
    previous_idle = int(previous.get("idle", idle))
    delta_total = total - previous_total
    delta_idle = idle - previous_idle
    usage = 0.0 if delta_total <= 0 else max(0.0, min(100.0, (delta_total - delta_idle) * 100 / delta_total))
    load_values = _read_text("/proc/loadavg", "0 0 0").split()
    loads = [float(value) for value in load_values[:3]] if len(load_values) >= 3 else [0.0, 0.0, 0.0]
    return {
        "usage_percent": round(usage, 2),
        "temperature_c": _temperature(),
        "load_1": loads[0],
        "load_5": loads[1],
        "load_15": loads[2],
        "_state": {"total": total, "idle": idle},
    }


def _temperature() -> float | None:
    candidates = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    candidates.extend(sorted(Path("/sys/class/hwmon").glob("hwmon*/temp*_input")))
    for candidate in candidates:
        # fmt: off
        try:
            value = float(candidate.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        # fmt: on
        if value > 200:
            value /= 1000
        if -50 <= value <= 150:
            return round(value, 2)
    return None


def _memory() -> dict[str, Any]:
    values: dict[str, int] = {}
    for line in _read_text("/proc/meminfo").splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        number = value.strip().split()[0] if value.strip() else "0"
        if number.isdigit():
            values[key] = int(number) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "used_percent": round(used * 100 / total, 2) if total else 0.0,
    }


def _unescape_mount(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 8))
        except ValueError:
            return match.group(0)

    return re.sub(r"\\([0-7]{3})", replace, value)


def _filesystems(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    include = set(config.get("filesystem_include", []))
    exclude = set(config.get("filesystem_exclude", []))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in _read_text("/proc/self/mountinfo").splitlines():
        separator = line.find(" - ")
        if separator < 0:
            continue
        before = line[:separator].split()
        after = line[separator + 3 :].split()
        if len(before) < 6 or len(after) < 2:
            continue
        mount = _unescape_mount(before[4])
        fstype = after[0]
        device = _unescape_mount(after[1])
        if fstype in PSEUDO_FILESYSTEMS or mount in exclude or (include and mount not in include):
            continue
        if mount in seen:
            continue
        try:
            if not hasattr(os, "statvfs"):
                continue
            stats = os.statvfs(mount)  # type: ignore[attr-defined]
        except OSError:
            continue
        total = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        used = max(0, total - available)
        mount_options = set(before[5].split(","))
        result.append(
            {
                "mount": mount,
                "device": device,
                "fstype": fstype,
                "total_bytes": total,
                "used_bytes": used,
                "used_percent": round(used * 100 / total, 2) if total else 0.0,
                "readonly": "ro" in mount_options,
            }
        )
        seen.add(mount)
    return result


def _network(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    include = set(config.get("network_include", []))
    exclude = set(config.get("network_exclude", []))
    result: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []
    for name in names:
        if name == "lo" or name in exclude or (include and name not in include):
            continue
        rx = _read_text(f"/sys/class/net/{name}/statistics/rx_bytes", "0")
        tx = _read_text(f"/sys/class/net/{name}/statistics/tx_bytes", "0")
        result.append(
            {
                "interface": name,
                "rx_bytes": int(rx) if rx.isdigit() else 0,
                "tx_bytes": int(tx) if tx.isdigit() else 0,
                "operstate": _read_text(f"/sys/class/net/{name}/operstate", "unknown"),
            }
        )
    return result


def _service_name_valid(name: str) -> bool:
    return bool(SERVICE_RE.fullmatch(name)) and not any(char in name for char in ";|&$`()<>/\\\n\r\t")


def _config() -> dict[str, Any]:
    config = _read_json(
        CONFIG_PATH,
        {
            "schema_version": SCHEMA_VERSION,
            "services": [],
            "filesystem_include": [],
            "filesystem_exclude": [],
            "network_include": [],
            "network_exclude": [],
        },
    )
    services = config.get("services", [])
    config["services"] = [name for name in services if isinstance(name, str) and _service_name_valid(name)]
    return config


def _service_allowed(name: str, config: Mapping[str, Any]) -> None:
    if not _service_name_valid(name):
        raise AgentError("service_invalid")
    if name not in config.get("services", []):
        raise AgentError("service_not_allowlisted")


def _service_status(name: str, config: Mapping[str, Any]) -> dict[str, Any]:
    _service_allowed(name, config)
    result = _run(["/bin/systemctl", "show", name, "--property=ActiveState,SubState", "--no-page"])
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    state = values.get("ActiveState", "unknown")
    substate = values.get("SubState", "unknown")
    return {
        "name": name,
        "active": result.returncode == 0 and state == "active",
        "state": state,
        "substate": substate,
    }


def _optional_service_status() -> dict[str, Any] | None:
    """Read the fixed Tailscale service state without requiring configuration."""

    try:
        result = _run(
            [
                "/bin/systemctl",
                "show",
                TAILSCALE_SERVICE,
                "--property=LoadState,ActiveState,SubState",
                "--no-page",
            ]
        )
    except AgentError:
        return None
    if result.returncode != 0:
        return None
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if values.get("LoadState") in {None, "not-found"}:
        return None
    state = values.get("ActiveState", "unknown")
    substate = values.get("SubState", "unknown")
    return {
        "name": TAILSCALE_SERVICE,
        "active": state == "active",
        "state": state,
        "substate": substate,
    }


def _job_state(state: dict[str, Any]) -> dict[str, Any] | None:
    job = state.get("job")
    if not isinstance(job, dict):
        return None
    if job.get("state") != "running":
        return job
    unit = job.get("unit")
    if not isinstance(unit, str) or not JOB_UNIT_RE.fullmatch(unit):
        job["state"] = "failed"
        job["summary"] = "Invalid package job state"
        return job
    result = _run(
        [
            "/bin/systemctl",
            "show",
            unit,
            "--property=ActiveState,SubState,ExecMainStatus",
            "--no-page",
        ]
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    active = values.get("ActiveState")
    if active in {"active", "activating", "deactivating"}:
        return job
    if active == "inactive" and values.get("SubState") == "dead":
        code = values.get("ExecMainStatus", "0")
        job["exit_code"] = int(code) if code.isdigit() else None
        job["state"] = "succeeded" if job["exit_code"] == 0 else "failed"
        job["finished_at"] = _now()
        label = PACKAGE_JOB_LABELS.get(str(job.get("type")), "Package job")
        job["summary"] = f"{label} completed" if job["state"] == "succeeded" else f"{label} failed"
    return job


def _validate_sudoers_policy(content: str, version: str) -> None:
    """Validate the complete Pi Manager policy before it can be activated."""

    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SUDOERS_BYTES:
        raise AgentError("sudoers_policy_invalid")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise AgentError("sudoers_policy_invalid")

    expected_marker = f"# Pi Manager sudo policy version: {version}"
    comments = [line.strip() for line in content.splitlines() if line.strip().startswith("#")]
    if comments.count(expected_marker) != 1:
        raise AgentError("sudoers_policy_invalid")

    policy_lines = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(policy_lines) != 1:
        raise AgentError("sudoers_policy_invalid")
    policy_username, separator, command_text = policy_lines[0].partition(" ALL=(root) NOPASSWD: ")
    if not separator or not SUDOERS_USERNAME_RE.fullmatch(policy_username):
        raise AgentError("sudoers_policy_invalid")
    if tuple(command_text.split(", ")) != SUDOERS_POLICY_COMMANDS:
        raise AgentError("sudoers_policy_invalid")


def _policy_version() -> str:
    """Return the version of the current valid policy, or ``unknown``."""

    content = _read_text(REMOTE_SUDOERS_PATH)
    matches = [
        match.group(1)
        for line in content.splitlines()
        if (match := SUDOERS_POLICY_VERSION_RE.fullmatch(line.strip())) is not None
    ]
    if len(matches) != 1:
        return "unknown"
    version = matches[0]
    try:
        _validate_sudoers_policy(content, version)
    except AgentError:
        return "unknown"
    return version


def _status() -> dict[str, Any]:
    config = _config()
    state = _read_json(STATE_PATH, {})
    cpu = _cpu(state)
    state["cpu"] = cpu.pop("_state")
    job = _job_state(state)
    state["job"] = job
    _write_json(STATE_PATH, state)
    services: list[dict[str, Any]] = []
    for name in config.get("services", []):
        try:
            services.append(_service_status(name, config))
        except AgentError:
            services.append({"name": name, "active": False, "state": "unknown", "substate": "unknown"})
    if not any(item["name"] == TAILSCALE_SERVICE for item in services):
        tailscale = _optional_service_status()
        if tailscale is not None:
            services.append(tailscale)
    reboot_required = Path("/var/run/reboot-required").exists()
    updates = dict(state.get("updates", {}))
    updates.setdefault("last_checked", None)
    updates.setdefault("available", 0)
    updates.setdefault("security", 0)
    updates["reboot_required"] = reboot_required
    return {
        "agent_version": AGENT_VERSION,
        "policy_version": _policy_version(),
        "machine": _machine(),
        "cpu": cpu,
        "memory": _memory(),
        "filesystems": _filesystems(config),
        "network": _network(config),
        "updates": updates,
        "services": services,
        "job": _public_job(job),
        "maintenance": _public_maintenance(state.get("maintenance")),
    }


def _public_job(job: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    return {
        "id": job.get("id"),
        "type": job.get("type"),
        "state": job.get("state"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "exit_code": job.get("exit_code"),
        "summary": job.get("summary"),
    }


def _public_maintenance(value: Any) -> dict[str, Any]:
    """Return only bounded, known maintenance state from the state file."""

    if not isinstance(value, Mapping):
        return {"previews": [], "audit": None, "holds": None, "failed_services": None}

    raw_previews = value.get("previews", {})
    preview_values = list(raw_previews.values()) if isinstance(raw_previews, Mapping) else raw_previews
    previews: list[dict[str, Any]] = []
    if isinstance(preview_values, list):
        for raw in preview_values[:8]:
            if not isinstance(raw, Mapping):
                continue
            operation = raw.get("operation")
            if not isinstance(operation, str) or operation not in PACKAGE_PREVIEW_COMMANDS:
                continue
            previews.append(
                {
                    "operation": operation,
                    "last_checked": _bounded_text(raw.get("last_checked")),
                    "upgraded": _bounded_count(raw.get("upgraded")),
                    "newly_installed": _bounded_count(raw.get("newly_installed")),
                    "to_remove": _bounded_count(raw.get("to_remove")),
                    "not_upgraded": _bounded_count(raw.get("not_upgraded")),
                    "reboot_required": raw.get("reboot_required") is True,
                    "summary": _bounded_text(raw.get("summary")),
                }
            )

    raw_audit = value.get("audit")
    audit = None
    if isinstance(raw_audit, Mapping):
        audit = {
            "last_checked": _bounded_text(raw_audit.get("last_checked")),
            "healthy": raw_audit.get("healthy") is True,
            "dpkg_issues": _bounded_count(raw_audit.get("dpkg_issues")),
            "apt_healthy": raw_audit.get("apt_healthy") is True,
            "summary": _bounded_text(raw_audit.get("summary")),
        }

    raw_holds = value.get("holds")
    holds = None
    if isinstance(raw_holds, Mapping):
        holds = {
            "last_checked": _bounded_text(raw_holds.get("last_checked")),
            "packages": _bounded_names(raw_holds.get("packages")),
        }

    raw_failed = value.get("failed_services")
    failed_services = None
    if isinstance(raw_failed, Mapping):
        failed_services = {
            "last_checked": _bounded_text(raw_failed.get("last_checked")),
            "services": _bounded_names(raw_failed.get("services")),
        }

    return {
        "previews": previews,
        "audit": audit,
        "holds": holds,
        "failed_services": failed_services,
    }


def _bounded_text(value: Any, limit: int = 512) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _bounded_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _bounded_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [name[:256] for name in value[:256] if isinstance(name, str) and PACKAGE_NAME_RE.fullmatch(name)]


def _write_maintenance(key: str, value: Mapping[str, Any]) -> None:
    state = _read_json(STATE_PATH, {})
    maintenance = state.get("maintenance")
    if not isinstance(maintenance, dict):
        maintenance = {}
    maintenance[key] = dict(value)
    state["maintenance"] = maintenance
    _write_json(STATE_PATH, state)


def _refresh_package_index(*, error_code: str) -> subprocess.CompletedProcess[str]:
    result = _run(
        ["/usr/bin/apt-get", "update", "-q"],
        timeout=300,
        env={"LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AgentError(error_code)
    return result


def _apt_counts(output: str) -> tuple[int, int, int, int]:
    match = re.search(
        r"(\d+)\s+upgraded,\s+(\d+)\s+newly installed,\s+(\d+)\s+to remove\s+and\s+(\d+)\s+not upgraded",
        output,
    )
    if match:
        return tuple(int(value) for value in match.groups())  # type: ignore[return-value]
    return (
        sum(1 for line in output.splitlines() if line.startswith("Inst ")),
        0,
        sum(1 for line in output.splitlines() if line.startswith("Remv ")),
        0,
    )


def _preview_package(operation: str) -> dict[str, Any]:
    command = PACKAGE_PREVIEW_COMMANDS.get(operation)
    if command is None:
        raise AgentError("operation_not_allowed")
    if operation in {"preview-upgrade", "preview-dist-upgrade"}:
        _refresh_package_index(error_code="package_preview_failed")
    result = _run(command, timeout=180, env={"LC_ALL": "C"})
    if result.returncode != 0:
        raise AgentError("package_preview_failed")
    upgraded, newly_installed, to_remove, not_upgraded = _apt_counts(result.stdout + "\n" + result.stderr)
    preview = {
        "operation": operation,
        "last_checked": _now(),
        "upgraded": upgraded,
        "newly_installed": newly_installed,
        "to_remove": to_remove,
        "not_upgraded": not_upgraded,
        "reboot_required": Path("/var/run/reboot-required").exists(),
        "summary": (
            f"{upgraded} upgrades, {newly_installed} new packages, {to_remove} removals, {not_upgraded} held back"
        ),
    }
    state = _read_json(STATE_PATH, {})
    maintenance = state.get("maintenance")
    if not isinstance(maintenance, dict):
        maintenance = {}
    previews = maintenance.get("previews")
    if not isinstance(previews, dict):
        previews = {}
    previews[operation] = preview
    maintenance["previews"] = previews
    state["maintenance"] = maintenance
    _write_json(STATE_PATH, state)
    return preview


def _audit_packages() -> dict[str, Any]:
    dpkg = _run(["/usr/bin/dpkg", "--audit"], timeout=60, env={"LC_ALL": "C"})
    apt = _run(["/usr/bin/apt-get", "check"], timeout=120, env={"LC_ALL": "C"})
    dpkg_issues = min(256, sum(1 for line in dpkg.stdout.splitlines() if line.strip()))
    audit = {
        "last_checked": _now(),
        "healthy": dpkg.returncode == 0 and dpkg_issues == 0 and apt.returncode == 0,
        "dpkg_issues": dpkg_issues,
        "apt_healthy": apt.returncode == 0,
        "summary": (
            "Package database healthy"
            if dpkg.returncode == 0 and dpkg_issues == 0 and apt.returncode == 0
            else "Package database needs attention"
        ),
    }
    _write_maintenance("audit", audit)
    return audit


def _show_package_holds() -> dict[str, Any]:
    result = _run(["/usr/bin/apt-mark", "showhold"], timeout=60, env={"LC_ALL": "C"})
    if result.returncode != 0:
        raise AgentError("package_holds_failed")
    packages = _bounded_names([line.strip() for line in result.stdout.splitlines() if line.strip()])
    holds = {"last_checked": _now(), "packages": packages}
    _write_maintenance("holds", holds)
    return holds


def _failed_services() -> dict[str, Any]:
    result = _run(
        ["/bin/systemctl", "list-units", "--failed", "--type=service", "--no-legend", "--no-pager"],
        timeout=60,
        env={"LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AgentError("failed_services_query_failed")
    services: list[str] = []
    for line in result.stdout.splitlines():
        name = line.split(maxsplit=1)[0] if line.split() else ""
        if name and _service_name_valid(name) and name not in services:
            services.append(name)
        if len(services) == 256:
            break
    failed = {"last_checked": _now(), "services": services}
    _write_maintenance("failed_services", failed)
    return failed


def _check_updates() -> dict[str, Any]:
    state = _read_json(STATE_PATH, {})
    _refresh_package_index(error_code="package_check_failed")
    result = _run(
        ["/usr/bin/apt-get", "-s", "-q", "upgrade"],
        timeout=120,
        env={"LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AgentError("package_check_failed")
    available = 0
    match = re.search(r"(\d+) upgraded,", result.stdout)
    if match:
        available = int(match.group(1))
    updates = {
        "last_checked": _now(),
        "available": available,
        "security": _security_count(result.stdout),
    }
    state["updates"] = updates
    _write_json(STATE_PATH, state)
    return updates


def _security_count(output: str) -> int:
    return sum(1 for line in output.splitlines() if "security" in line.lower() and line.startswith("Inst "))


def _run_package_job(job_type: str) -> int:
    """Run one fixed package job from its root-only systemd worker."""

    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise AgentError("job_worker_requires_root")
    if job_type not in PACKAGE_JOB_COMMANDS:
        raise AgentError("operation_not_allowed")
    if job_type in PACKAGE_JOBS_REQUIRING_REFRESH:
        _refresh_package_index(error_code="package_refresh_failed")
    result = _run(
        PACKAGE_JOB_COMMANDS[job_type],
        timeout=3600,
        env={"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AgentError("package_job_failed")
    return 0


def _start_package_job(job_type: str) -> dict[str, Any]:
    if job_type not in PACKAGE_JOB_COMMANDS:
        raise AgentError("operation_not_allowed")
    with _update_lock():
        state = _read_json(STATE_PATH, {})
        current = _job_state(state)
        if current and current.get("state") == "running":
            raise AgentError("update_already_running")
        identifier = uuid.uuid4().hex
        unit = f"pi-manager-job-{identifier}"
        result = _run(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--no-block",
                f"--unit={unit}",
                "--property=Type=oneshot",
                REMOTE_CTL_PATH,
                "job-worker",
                job_type,
            ],
            timeout=15,
        )
        if result.returncode != 0:
            raise AgentError("package_job_start_failed")
        job = {
            "id": f"pm-{dt.datetime.now(dt.UTC).strftime('%Y%m%d-%H%M%S')}-{identifier[:4]}",
            "type": job_type,
            "state": "running",
            "started_at": _now(),
            "finished_at": None,
            "exit_code": None,
            "summary": f"{PACKAGE_JOB_LABELS[job_type]} started",
            "unit": unit,
        }
        state["job"] = job
        _write_json(STATE_PATH, state)
        return _public_job(job) or {}


def _start_update() -> dict[str, Any]:
    """Compatibility wrapper for the original normal update operation."""

    return _start_package_job("upgrade")


def _configure_services(encoded: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        if len(raw) > MAX_JSON_BYTES:
            raise AgentError("service_config_invalid")
        configuration = json.loads(raw.decode("utf-8"))
    except AgentError:
        raise
    except (ValueError, UnicodeError, json.JSONDecodeError) as err:
        raise AgentError("service_config_invalid") from err
    if isinstance(configuration, list):
        configuration = {"services": configuration}
    if not isinstance(configuration, dict):
        raise AgentError("service_config_invalid")
    services = configuration.get("services", [])
    if not isinstance(services, list) or len(services) > 128:
        raise AgentError("service_config_invalid")
    normalized: list[str] = []
    for name in services:
        if not isinstance(name, str) or not _service_name_valid(name):
            raise AgentError("service_invalid")
        if name not in normalized:
            normalized.append(name)
    config = _config()
    config["services"] = normalized
    for key in ("filesystem_include", "filesystem_exclude", "network_include", "network_exclude"):
        values = configuration.get(key, config.get(key, []))
        if (
            not isinstance(values, list)
            or len(values) > 256
            or any(
                not isinstance(value, str) or not value or len(value) > 4096 or any(ord(char) < 32 for char in value)
                for value in values
            )
        ):
            raise AgentError("filter_config_invalid")
        config[key] = list(dict.fromkeys(values))
    config["schema_version"] = SCHEMA_VERSION
    _write_json(CONFIG_PATH, config)
    os.chmod(CONFIG_PATH, 0o640)
    return {
        "configured_services": normalized,
        "filesystem_include": config["filesystem_include"],
        "filesystem_exclude": config["filesystem_exclude"],
        "network_include": config["network_include"],
        "network_exclude": config["network_exclude"],
    }


def _configure_trust(encoded_secret: str) -> dict[str, Any]:
    try:
        secret = base64.b64decode(encoded_secret.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as err:
        raise AgentError("trust_config_invalid") from err
    if len(secret) != 32:
        raise AgentError("trust_config_invalid")
    existing = _read_json(TRUST_PATH, {})
    existing_secret = existing.get("hmac_secret")
    if isinstance(existing_secret, str):
        if not hmac.compare_digest(existing_secret, encoded_secret):
            raise AgentError("trust_already_configured")
        return {"configured": True, "unchanged": True}
    _write_json(TRUST_PATH, {"schema_version": SCHEMA_VERSION, "hmac_secret": encoded_secret})
    os.chmod(TRUST_PATH, 0o640)
    return {"configured": True}


def _decode_sudoers_policy(encoded: str, version: str) -> bytes:
    try:
        policy_source = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as err:
        raise AgentError("sudoers_policy_invalid") from err
    if len(policy_source) > MAX_SUDOERS_BYTES:
        raise AgentError("sudoers_policy_invalid")
    try:
        policy_text = policy_source.decode("utf-8")
    except UnicodeDecodeError as err:
        raise AgentError("sudoers_policy_invalid") from err
    _validate_sudoers_policy(policy_text, version)
    return policy_source


def _set_root_ownership(path: Path) -> None:
    chown = getattr(os, "chown", None)
    if chown is None:
        return
    try:
        chown(path, 0, 0)
    except OSError as err:
        raise AgentError("sudoers_write_failed") from err


def _install_sudoers_policy(content: str, version: str) -> None:
    """Validate with visudo, then atomically activate the owned policy file."""

    effective_uid = getattr(os, "geteuid", None)
    if effective_uid is not None and effective_uid() != 0:
        raise AgentError("sudoers_policy_requires_root")
    _validate_sudoers_policy(content, version)
    target = Path(REMOTE_SUDOERS_PATH)
    if target.is_symlink():
        raise AgentError("sudoers_target_invalid")

    temporary: Path | None = None
    try:
        target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".upgrade",
            dir=str(target.parent),
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o440)
        _set_root_ownership(temporary)
        result = _run(["/usr/sbin/visudo", "-cf", str(temporary)], timeout=30)
        if result.returncode != 0:
            raise AgentError("sudoers_validation_failed")
        if target.is_symlink():
            raise AgentError("sudoers_target_invalid")
        os.replace(temporary, target)
        temporary = None
        os.chmod(target, 0o440)
        _set_root_ownership(target)
    except AgentError:
        raise
    except OSError as err:
        raise AgentError("sudoers_write_failed") from err
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _upgrade_helper(
    version: str,
    agent_encoded: str,
    ctl_encoded: str,
    signature: str,
    sudoers_encoded: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise AgentError("helper_version_invalid")
    trust = _read_json(TRUST_PATH, {})
    encoded_secret = trust.get("hmac_secret")
    if not isinstance(encoded_secret, str):
        raise AgentError("helper_upgrade_not_trusted")
    try:
        secret = base64.b64decode(encoded_secret.encode("ascii"), validate=True)
        agent_source = base64.b64decode(agent_encoded.encode("ascii"), validate=True)
        ctl_source = base64.b64decode(ctl_encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as err:
        raise AgentError("helper_upgrade_invalid") from err
    if len(secret) != 32 or len(agent_source) > 512 * 1024 or len(ctl_source) > 64 * 1024:
        raise AgentError("helper_upgrade_invalid")
    try:
        agent_text = agent_source.decode("utf-8")
        ctl_text = ctl_source.decode("utf-8")
    except UnicodeDecodeError as err:
        raise AgentError("helper_upgrade_invalid") from err
    if f'AGENT_VERSION = "{version}"' not in agent_text:
        raise AgentError("helper_upgrade_invalid")
    try:
        compile(agent_text, str(REMOTE_AGENT_PATH), "exec")
        compile(ctl_text, str(REMOTE_CTL_PATH), "exec")
    except SyntaxError as err:
        raise AgentError("helper_upgrade_invalid") from err
    sudoers_source: bytes | None = None
    if sudoers_encoded is not None:
        sudoers_source = _decode_sudoers_policy(sudoers_encoded, version)
    message = version.encode("utf-8") + b"\0" + agent_source + b"\0" + ctl_source
    if sudoers_source is not None:
        message += b"\0" + sudoers_source
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AgentError("helper_upgrade_signature_invalid")
    agent_path = Path(REMOTE_AGENT_PATH)
    ctl_path = Path(REMOTE_CTL_PATH)
    agent_temporary = agent_path.with_name(f".{agent_path.name}.{os.getpid()}.upgrade")
    ctl_temporary = ctl_path.with_name(f".{ctl_path.name}.{os.getpid()}.upgrade")
    try:
        agent_temporary.write_bytes(agent_source)
        ctl_temporary.write_bytes(ctl_source)
        os.chmod(agent_temporary, 0o644)
        os.chmod(ctl_temporary, 0o755)
        os.replace(agent_temporary, agent_path)
        os.replace(ctl_temporary, ctl_path)
    except OSError as err:
        for path in (agent_temporary, ctl_temporary):
            try:
                path.unlink()
            except OSError:
                pass
        raise AgentError("helper_upgrade_write_failed") from err
    result: dict[str, Any] = {
        "upgraded": True,
        "agent_version": version,
        "agent_sha256": hashlib.sha256(agent_source).hexdigest(),
    }
    if sudoers_source is not None:
        _install_sudoers_policy(sudoers_source.decode("utf-8"), version)
        result["policy_version"] = version
    return result


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pi-managerctl", add_help=False)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in (
        "status",
        "check-updates",
        "update",
        "upgrade",
        "dist-upgrade",
        "preview-upgrade",
        "preview-dist-upgrade",
        "preview-autoremove",
        "audit-packages",
        "show-package-holds",
        "failed-services",
        "configure-packages",
        "repair-packages",
        "autoremove",
        "autoclean",
        "clean-cache",
        "reboot",
        "shutdown",
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--json", action="store_true", required=True)
    worker = subparsers.add_parser("job-worker")
    worker.add_argument("job_type", choices=tuple(PACKAGE_JOB_COMMANDS))
    for name in ("service-status", "service-restart", "service-validate"):
        sub = subparsers.add_parser(name)
        sub.add_argument("name")
        sub.add_argument("--json", action="store_true", required=True)
    configure = subparsers.add_parser("configure-services")
    configure.add_argument("--services-json", required=True)
    configure.add_argument("--json", action="store_true", required=True)
    trust = subparsers.add_parser("configure-trust")
    trust.add_argument("--secret", required=True)
    trust.add_argument("--json", action="store_true", required=True)
    upgrade = subparsers.add_parser("upgrade-helper")
    upgrade.add_argument("--version", required=True)
    upgrade.add_argument("--agent", required=True)
    upgrade.add_argument("--ctl", required=True)
    upgrade.add_argument("--signature", required=True)
    upgrade.add_argument("--sudoers")
    upgrade.add_argument("--json", action="store_true", required=True)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit helper operation and emit versioned JSON."""

    try:
        args = _parse_args(argv or sys.argv[1:])
        if args.operation == "status":
            return _emit(_status())
        if args.operation == "check-updates":
            return _emit({"updates": _check_updates()})
        if args.operation in {"update", "upgrade"}:
            return _emit({"job": _start_update()})
        if args.operation == "dist-upgrade":
            return _emit({"job": _start_package_job("dist-upgrade")})
        if args.operation in PACKAGE_PREVIEW_COMMANDS:
            return _emit({"preview": _preview_package(args.operation)})
        if args.operation == "audit-packages":
            return _emit({"audit": _audit_packages()})
        if args.operation == "show-package-holds":
            return _emit({"holds": _show_package_holds()})
        if args.operation == "failed-services":
            return _emit({"failed_services": _failed_services()})
        if args.operation in {
            "configure-packages",
            "repair-packages",
            "autoremove",
            "autoclean",
            "clean-cache",
        }:
            return _emit({"job": _start_package_job(args.operation)})
        if args.operation == "job-worker":
            return _run_package_job(args.job_type)
        if args.operation == "reboot":
            result = _run(["/bin/systemctl", "reboot"], timeout=10)
            return _emit(
                {"accepted": result.returncode == 0, "action": "reboot"},
                returncode=result.returncode,
            )
        if args.operation == "shutdown":
            result = _run(["/bin/systemctl", "poweroff"], timeout=10)
            return _emit(
                {"accepted": result.returncode == 0, "action": "shutdown"},
                returncode=result.returncode,
            )
        config = _config()
        if args.operation == "service-status":
            return _emit({"service": _service_status(args.name, config)})
        if args.operation == "service-validate":
            if not _service_name_valid(args.name):
                raise AgentError("service_invalid")
            result = _run(["/bin/systemctl", "show", args.name, "--property=LoadState", "--no-page"])
            if result.returncode != 0 or "LoadState=not-found" in result.stdout:
                raise AgentError("service_not_found")
            return _emit({"valid": True, "name": args.name})
        if args.operation == "service-restart":
            _service_allowed(args.name, config)
            result = _run(["/bin/systemctl", "restart", args.name], timeout=60)
            if result.returncode != 0:
                raise AgentError("service_restart_failed")
            return _emit({"accepted": True, "service": args.name})
        if args.operation == "configure-services":
            return _emit(_configure_services(args.services_json))
        if args.operation == "configure-trust":
            return _emit(_configure_trust(args.secret))
        if args.operation == "upgrade-helper":
            return _emit(_upgrade_helper(args.version, args.agent, args.ctl, args.signature, args.sudoers))
        raise AgentError("operation_not_allowed")
    except AgentError as err:
        print(f"pi-manager: {err.code}", file=sys.stderr)
        return _emit({"error": {"code": err.code, "message": err.code}}, returncode=1)
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        del err
        print("pi-manager: operation failed", file=sys.stderr)
        return _emit({"error": {"code": "operation_failed", "message": "operation_failed"}}, returncode=1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
