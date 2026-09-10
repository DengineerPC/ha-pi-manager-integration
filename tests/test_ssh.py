from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from custom_components.pi_manager import ssh
from custom_components.pi_manager.errors import HostFingerprintMismatch
from custom_components.pi_manager.models import CommandResult
from custom_components.pi_manager.ssh import AsyncSSHClient, _bounded, _verify_fingerprint


def test_command_output_is_bounded() -> None:
    value = _bounded("x" * 400_000)
    assert len(value.encode()) <= 256 * 1024


def test_command_result_ok() -> None:
    assert CommandResult(0, "", "").ok
    assert not CommandResult(1, "", "").ok


def test_fingerprint_verification_requires_first_trust_or_exact_match() -> None:
    assert _verify_fingerprint("SHA256:one", None, True) is True
    assert _verify_fingerprint("SHA256:one", "SHA256:one", False) is True
    with pytest.raises(HostFingerprintMismatch):
        _verify_fingerprint("SHA256:one", None, False)
    with pytest.raises(HostFingerprintMismatch):
        _verify_fingerprint("SHA256:two", "SHA256:one", False)


@pytest.mark.asyncio
async def test_asyncssh_import_runs_off_home_assistant_event_loop() -> None:
    caller_thread = threading.get_ident()
    observed_thread: int | None = None

    def fake_import(name: str) -> SimpleNamespace:
        nonlocal observed_thread
        assert name == "asyncssh"
        observed_thread = threading.get_ident()
        return SimpleNamespace()

    original_import = ssh.importlib.import_module
    ssh.importlib.import_module = fake_import
    try:
        await ssh._async_import_asyncssh()
    finally:
        ssh.importlib.import_module = original_import

    assert observed_thread is not None
    assert observed_thread != caller_thread


@pytest.mark.asyncio
async def test_sudo_prompt_argument_is_non_empty() -> None:
    class FakeConnection:
        command: str | None = None

        async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
            del kwargs
            self.command = command
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    client = AsyncSSHClient("example.test", 22, "user")
    connection = FakeConnection()
    client._connection = connection

    result = await client.run_sudo(["/bin/true"], password="temporary")

    assert result.ok
    assert connection.command == "/usr/bin/sudo -S -p= -- /bin/true"
