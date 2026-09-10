from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from custom_components.pi_manager.bootstrap import Bootstrapper
from custom_components.pi_manager.errors import BootstrapError
from custom_components.pi_manager.models import (
    BootstrapResult,
    CommandResult,
    HostDiscovery,
    KeyMaterial,
)


class FakeClient:
    host = "pi.example"
    port = 22
    username = "ha"
    fingerprint = "SHA256:trusted"

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.uploads: dict[str, str] = {}
        self.closed = False
        self.fail_install_once = False

    async def connect(self, **kwargs) -> None:
        assert "password" not in kwargs
        assert kwargs["expected_fingerprint"] == self.fingerprint

    async def run(self, args: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        del timeout
        self.commands.append(tuple(args))
        return CommandResult(0, "", "")

    async def run_sudo(self, args: Sequence[str], *, password: str, timeout: float = 30.0) -> CommandResult:
        del password, timeout
        self.commands.append(("sudo", *args))
        if self.fail_install_once and tuple(args[:2]) == ("/usr/bin/install", "-D"):
            self.fail_install_once = False
            return CommandResult(1, "", "install failed")
        return CommandResult(0, "", "")

    async def run_sudo_nopass(self, args: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        del timeout
        self.commands.append(("sudo-nopass", *args))
        if tuple(args[-2:]) == ("status", "--json"):
            return CommandResult(
                0,
                '{"schema_version":1,"agent_version":"0.1.0","machine":{"machine_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","hostname":"pi","model":"Pi","os":"Debian","kernel":"k","arch":"aarch64","uptime_seconds":1},"cpu":{},"memory":{},"filesystems":[],"network":[],"updates":{},"services":[],"job":null}',
                "",
            )
        return CommandResult(0, "", "")

    async def upload_text(self, path: str, content: str, *, mode: int = 0o600) -> None:
        del mode
        self.uploads[path] = content

    async def close(self) -> None:
        self.closed = True


class FakeKeyStore:
    async def async_create_for_machine(self, machine_id: str) -> KeyMaterial:
        return KeyMaterial(machine_id, "PRIVATE", "ssh-ed25519 PUBLIC")


def discovery() -> HostDiscovery:
    return HostDiscovery(
        machine_id="a" * 32,
        hostname="pi",
        os_name="Debian",
        os_version="12",
        architecture="aarch64",
        python_version="Python 3.13",
        systemd_version="systemd 257",
        apt_version="apt 3",
        sudo_available=True,
        helper_installed=False,
        helper_version=None,
        target_writable=True,
        fingerprint="SHA256:trusted",
    )


@pytest.mark.asyncio
async def test_bootstrap_uses_no_password_in_final_commands(tmp_path) -> None:
    initial = FakeClient()
    final = FakeClient()
    bootstrapper = Bootstrapper(config_dir=tmp_path, client_factory=lambda host, port, username: final)
    bootstrapper.key_store = FakeKeyStore()
    result = await bootstrapper.async_bootstrap(
        initial,
        discovery=discovery(),
        username="ha",
        password="secret-bootstrap-password",
        options={"monitored_services": []},
    )
    assert isinstance(result, BootstrapResult)
    assert all("secret-bootstrap-password" not in " ".join(command) for command in initial.commands + final.commands)
    assert result.key_id == "a" * 32
    assert final.closed


@pytest.mark.asyncio
async def test_bootstrap_rejects_read_only_target(tmp_path) -> None:
    initial = FakeClient()
    bootstrapper = Bootstrapper(config_dir=tmp_path, client_factory=lambda host, port, username: FakeClient())
    bootstrapper.key_store = FakeKeyStore()
    readonly = replace(discovery(), target_writable=False)
    with pytest.raises(Exception, match="target_read_only"):
        await bootstrapper.async_bootstrap(initial, discovery=readonly, username="ha", password="secret")


@pytest.mark.asyncio
async def test_partial_bootstrap_can_retry_with_the_same_owned_material(tmp_path) -> None:
    initial = FakeClient()
    initial.fail_install_once = True
    final = FakeClient()
    bootstrapper = Bootstrapper(config_dir=tmp_path, client_factory=lambda host, port, username: final)
    bootstrapper.key_store = FakeKeyStore()

    with pytest.raises(BootstrapError, match="helper_install"):
        await bootstrapper.async_bootstrap(
            initial,
            discovery=discovery(),
            username="ha",
            password="secret",
            options={"monitored_services": []},
        )
    result = await bootstrapper.async_bootstrap(
        initial,
        discovery=discovery(),
        username="ha",
        password="secret",
        options={"monitored_services": []},
    )
    assert result.machine_id == "a" * 32
    append_commands = [command for command in initial.commands if command and command[0] == "/usr/bin/python3"]
    assert append_commands
    assert "if key not in lines:" in append_commands[0][2]
