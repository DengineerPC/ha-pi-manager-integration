"""Remote capability discovery before a config entry is committed."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping

from .const import REMOTE_AGENT_PATH, REMOTE_CTL_PATH
from .errors import HostDiscoveryError
from .models import HostDiscovery
from .ssh import SSHClientProtocol

_OS_LINE = re.compile(r"^(?P<key>[A-Z_]+)=(?P<value>.*)$")
_VERSION = re.compile(r"(?P<version>\d+(?:\.\d+)*)")
_MACHINE_ID = re.compile(r"^[a-fA-F0-9]{16,128}$")


async def discover_host(
    client: SSHClientProtocol,
    *,
    bootstrap_password: str | None = None,
) -> HostDiscovery:
    """Collect bounded host capabilities and reject unsupported prerequisites."""

    if not client.fingerprint:
        raise HostDiscoveryError("fingerprint_unavailable")
    os_result = await client.run(["/usr/bin/cat", "/etc/os-release"])
    if not os_result.ok:
        raise HostDiscoveryError("unsupported_os")
    os_info = _parse_os_release(os_result.stdout)
    os_id = os_info.get("ID", "").lower()
    os_like = os_info.get("ID_LIKE", "").lower()
    if not ({"debian", "raspbian", "ubuntu"} & ({os_id} | set(os_like.split()))):
        raise HostDiscoveryError("unsupported_os")

    architecture = (await _required_output(client, ["/usr/bin/uname", "-m"], "unsupported_architecture")).strip()
    if architecture not in {"aarch64", "arm64", "armv6l", "armv7l", "armhf", "x86_64", "amd64", "i386"}:
        raise HostDiscoveryError("unsupported_architecture")
    python_version = (await _required_output(client, ["/usr/bin/python3", "--version"], "missing_python")).strip()
    systemd_version = await _required_output(client, ["/usr/bin/systemctl", "--version"], "missing_systemd")
    apt_version = await _required_output(client, ["/usr/bin/apt-get", "--version"], "missing_apt")
    hostname = await _required_output(client, ["/bin/hostname"], "hostname_unavailable")
    machine_id = (await _required_output(client, ["/usr/bin/cat", "/etc/machine-id"], "machine_id_unavailable")).strip()
    if not _MACHINE_ID.fullmatch(machine_id):
        raise HostDiscoveryError("machine_id_unavailable")

    sudo_available = False
    if bootstrap_password is not None:
        sudo_result = await client.run_sudo(["/usr/bin/true"], password=bootstrap_password)
        sudo_available = sudo_result.ok
    else:
        sudo_available = (await client.run_sudo_nopass(["/usr/bin/true"])).ok
    if not sudo_available:
        raise HostDiscoveryError("sudo_unavailable")

    helper_check = await client.run(["/usr/bin/test", "-x", REMOTE_CTL_PATH])
    helper_installed = helper_check.ok
    helper_version: str | None = None
    if helper_installed:
        version_result = await client.run(["/usr/bin/grep", "-m1", "^AGENT_VERSION", REMOTE_AGENT_PATH])
        if version_result.ok:
            helper_version = _parse_agent_version(version_result.stdout)

    probe_path = f"/usr/local/.pi-manager-write-probe-{secrets.token_hex(8)}"
    writable_result = (
        await client.run_sudo(["/usr/bin/touch", probe_path], password=bootstrap_password)
        if bootstrap_password is not None
        else await client.run_sudo_nopass(["/usr/bin/touch", probe_path])
    )
    if bootstrap_password is not None:
        await client.run_sudo(["/bin/rm", "-f", probe_path], password=bootstrap_password)
    else:
        await client.run_sudo_nopass(["/bin/rm", "-f", probe_path])

    return HostDiscovery(
        machine_id=machine_id,
        hostname=hostname.strip(),
        os_name=os_info.get("PRETTY_NAME", os_info.get("NAME", "Unknown")).strip('"'),
        os_version=os_info.get("VERSION_ID", "unknown").strip('"'),
        architecture=architecture.strip(),
        python_version=python_version.strip(),
        systemd_version=systemd_version.splitlines()[0].strip(),
        apt_version=apt_version.splitlines()[0].strip(),
        sudo_available=sudo_available,
        helper_installed=helper_installed,
        helper_version=helper_version,
        target_writable=writable_result.ok,
        fingerprint=client.fingerprint,
        raw_capabilities=tuple(sorted(os_info)),
    )


def _parse_os_release(content: str) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        match = _OS_LINE.match(line.strip())
        if match:
            result[match.group("key")] = match.group("value")
    return result


async def _required_output(client: SSHClientProtocol, args: list[str], category: str) -> str:
    result = await client.run(args)
    if not result.ok or not result.stdout.strip():
        raise HostDiscoveryError(category)
    return result.stdout


def _parse_agent_version(content: str) -> str | None:
    match = _VERSION.search(content)
    return match.group("version") if match else None
