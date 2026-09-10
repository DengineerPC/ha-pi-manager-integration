from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pi_manager.const import CONF_POLL_INTERVAL
from custom_components.pi_manager.contract import parse_status
from custom_components.pi_manager.coordinator import PiManagerCoordinator
from custom_components.pi_manager.errors import HelperProtocolError, PiManagerConnectionError
from custom_components.pi_manager.models import CommandResult


def _status():
    fixture = Path(__file__).parent / "fixtures" / "healthy_status.json"
    return parse_status(json.loads(fixture.read_text(encoding="utf-8")))


class FakeRuntime:
    def __init__(self) -> None:
        self.entry = SimpleNamespace(entry_id="entry-1", async_on_unload=lambda _callback: None)
        self.options = {CONF_POLL_INTERVAL: 30, "enable_package_checks": False}
        self.status = _status()
        self.fail = False
        self.status_calls = 0
        self.helper_upgrade_attempted = False

    async def async_status(self):
        self.status_calls += 1
        if self.fail:
            raise PiManagerConnectionError("unavailable")
        return self.status

    async def async_check_updates_if_due(self):
        return None


@pytest.mark.asyncio
async def test_coordinator_uses_one_status_call_and_recovers_after_timeout(tmp_path) -> None:
    runtime = FakeRuntime()
    coordinator = PiManagerCoordinator(HomeAssistant(str(tmp_path)), runtime)

    refreshed = await coordinator._async_update_data()
    assert refreshed.machine.machine_id == "0123456789abcdef0123456789abcdef"
    assert runtime.status_calls == 1
    assert coordinator.last_error_category is None

    runtime.fail = True
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.last_error_category == "cannot_connect"

    runtime.fail = False
    await coordinator._async_update_data()
    assert coordinator.last_error_category is None
    assert runtime.status_calls == 3


@pytest.mark.asyncio
async def test_package_check_cadence_is_optional_and_bounded(monkeypatch) -> None:
    from custom_components.pi_manager.models import UpdateInfo
    from custom_components.pi_manager.runtime import PiManagerRuntime

    entry = SimpleNamespace(
        data={"key_id": "a" * 32},
        options={"enable_package_checks": True, "package_check_cadence": 900},
    )
    runtime = PiManagerRuntime(hass=None, entry=entry, key_store=object())
    calls = 0

    async def fake_check_updates(_runtime):
        nonlocal calls
        calls += 1
        return UpdateInfo("now", 1, 0, False)

    monkeypatch.setattr(PiManagerRuntime, "async_check_updates", fake_check_updates)
    assert await runtime.async_check_updates_if_due() is not None
    assert await runtime.async_check_updates_if_due() is None
    assert calls == 1


@pytest.mark.asyncio
async def test_runtime_rejects_a_status_document_from_another_machine(monkeypatch) -> None:
    from custom_components.pi_manager.runtime import PiManagerRuntime

    entry = SimpleNamespace(
        data={"machine_id": "a" * 32},
        options={"enable_package_checks": False},
    )
    runtime = PiManagerRuntime(hass=None, entry=entry, key_store=object())
    fixture = Path(__file__).parent / "fixtures" / "healthy_status.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["machine"]["machine_id"] = "b" * 32

    async def fake_run_helper(_runtime, _args):
        return CommandResult(0, json.dumps(payload), "")

    monkeypatch.setattr(PiManagerRuntime, "async_run_helper", fake_run_helper)
    with pytest.raises(HelperProtocolError, match="machine_identity_changed"):
        await runtime.async_status()
