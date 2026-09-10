from __future__ import annotations

from collections.abc import Sequence

import pytest

from custom_components.pi_manager.discovery import discover_host
from custom_components.pi_manager.errors import HostDiscoveryError
from custom_components.pi_manager.models import CommandResult


class FakeClient:
    fingerprint = "SHA256:test-fingerprint"

    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        del timeout
        key = tuple(args)
        self.calls.append(key)
        return self.results.get(key, CommandResult(1, "", "missing fake response"))

    async def run_sudo(self, args: Sequence[str], *, password: str, timeout: float = 30.0) -> CommandResult:
        del password, timeout
        self.calls.append(("sudo", *args))
        return CommandResult(0, "", "")

    async def run_sudo_nopass(self, args: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        del timeout
        self.calls.append(("sudo-nopass", *args))
        return CommandResult(0, "", "")


def make_client() -> FakeClient:
    return FakeClient(
        {
            ("/usr/bin/cat", "/etc/os-release"): CommandResult(
                0,
                'ID=debian\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\n',
                "",
            ),
            ("/usr/bin/uname", "-m"): CommandResult(0, "aarch64\n", ""),
            ("/usr/bin/python3", "--version"): CommandResult(0, "Python 3.13.0\n", ""),
            ("/usr/bin/systemctl", "--version"): CommandResult(0, "systemd 257\n", ""),
            ("/usr/bin/apt-get", "--version"): CommandResult(0, "apt 3.0.0\n", ""),
            ("/bin/hostname",): CommandResult(0, "pi-nas\n", ""),
            ("/usr/bin/cat", "/etc/machine-id"): CommandResult(0, "a" * 32 + "\n", ""),
            ("/usr/bin/test", "-x", "/usr/local/sbin/pi-managerctl"): CommandResult(1, "", ""),
            ("/usr/bin/test", "-w", "/usr/local"): CommandResult(0, "", ""),
        }
    )


@pytest.mark.asyncio
async def test_discover_supported_host() -> None:
    client = make_client()
    discovered = await discover_host(client, bootstrap_password="not-recorded")
    assert discovered.machine_id == "a" * 32
    assert discovered.architecture == "aarch64"
    assert discovered.sudo_available is True
    assert discovered.helper_installed is False


@pytest.mark.asyncio
async def test_discovery_accepts_raspberry_pi_1_architecture() -> None:
    client = make_client()
    client.results[("/usr/bin/uname", "-m")] = CommandResult(0, "armv6l\n", "")

    discovered = await discover_host(client, bootstrap_password="bootstrap")

    assert discovered.architecture == "armv6l"


@pytest.mark.asyncio
async def test_discovery_rejects_unknown_architecture() -> None:
    client = make_client()
    client.results[("/usr/bin/uname", "-m")] = CommandResult(0, "mips64\n", "")

    with pytest.raises(HostDiscoveryError, match="unsupported_architecture"):
        await discover_host(client, bootstrap_password="bootstrap")


@pytest.mark.asyncio
async def test_discovery_rejects_missing_apt() -> None:
    client = make_client()
    client.results[("/usr/bin/apt-get", "--version")] = CommandResult(1, "", "not found")
    with pytest.raises(HostDiscoveryError, match="missing_apt"):
        await discover_host(client, bootstrap_password="bootstrap")


@pytest.mark.asyncio
async def test_discovery_does_not_use_password_in_call_arguments() -> None:
    client = make_client()
    await discover_host(client, bootstrap_password="super-secret")
    assert all("super-secret" not in " ".join(call) for call in client.calls)


@pytest.mark.asyncio
async def test_discovery_rejects_invalid_machine_identity() -> None:
    client = make_client()
    client.results[("/usr/bin/cat", "/etc/machine-id")] = CommandResult(0, "not-a-machine-id\n", "")
    with pytest.raises(HostDiscoveryError, match="machine_id_unavailable"):
        await discover_host(client, bootstrap_password="bootstrap")
