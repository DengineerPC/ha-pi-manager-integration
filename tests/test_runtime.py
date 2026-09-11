from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.pi_manager.errors import HelperProtocolError, PiManagerConnectionError
from custom_components.pi_manager.models import CommandResult
from custom_components.pi_manager.runtime import PiManagerRuntime, _error_code


class FakeClient:
    def __init__(self, result: CommandResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.closed = False

    async def run_sudo_nopass(self, args, *, timeout: float = 30.0) -> CommandResult:
        del args, timeout
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def close(self) -> None:
        self.closed = True


def _runtime() -> PiManagerRuntime:
    entry = SimpleNamespace(data={"key_id": "a" * 32}, options={})
    return PiManagerRuntime(hass=None, entry=entry, key_store=object())


@pytest.mark.asyncio
async def test_channel_failure_drops_only_the_cached_client() -> None:
    runtime = _runtime()
    client = FakeClient(error=PiManagerConnectionError("channel closed"))
    runtime.client = client

    with pytest.raises(PiManagerConnectionError):
        await runtime.async_run_helper(["status", "--json"])

    assert runtime.client is None
    assert client.closed is True


@pytest.mark.asyncio
async def test_sudo_password_requirement_has_a_stable_redacted_error() -> None:
    runtime = _runtime()
    runtime.client = FakeClient(CommandResult(1, "", "sudo: a password is required\n"))

    with pytest.raises(HelperProtocolError, match="helper_privilege_denied") as caught:
        await runtime.async_run_helper(["preview-upgrade", "--json"])

    assert "password is required" not in str(caught.value)


def test_json_helper_error_takes_precedence_over_stderr_mapping() -> None:
    assert _error_code('{"error":{"code":"package_preview_failed"}}', "sudo: a password is required") == (
        "package_preview_failed"
    )
