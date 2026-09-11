from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from custom_components.pi_manager.const import HELPER_VERSION
from custom_components.pi_manager.contract import parse_status
from custom_components.pi_manager.models import KeyMaterial
from custom_components.pi_manager.upgrade import async_upgrade_if_needed

FIXTURE = Path(__file__).parent / "fixtures" / "healthy_status.json"


class FakeKeyStore:
    async def async_load_material(self, key_id: str) -> KeyMaterial:
        assert key_id == "0123456789abcdef0123456789abcdef"
        return KeyMaterial(key_id, "private", "public", base64.b64encode(b"x" * 32).decode())


class FakeEntry:
    data = {"key_id": "0123456789abcdef0123456789abcdef", "username": "denis"}


class FakeRuntime:
    def __init__(self, refreshed) -> None:
        self.entry = FakeEntry()
        self.key_store = FakeKeyStore()
        self.helper_upgrade_attempted = False
        self.refreshed = refreshed
        self.commands: list[list[str]] = []

    async def async_run_helper(self, args, *, serialized: bool = False):
        assert serialized
        self.commands.append(list(args))
        return None

    async def async_status(self):
        return self.refreshed


@pytest.mark.asyncio
async def test_older_helper_is_upgraded_once() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    old = parse_status({**payload, "agent_version": "0.0.1"})
    new = parse_status({**payload, "agent_version": HELPER_VERSION})
    runtime = FakeRuntime(new)
    result = await async_upgrade_if_needed(runtime, old)
    assert result.agent_version == HELPER_VERSION
    assert runtime.helper_upgrade_attempted is True
    assert len(runtime.commands) == 2
    assert runtime.commands[0][0:3] == ["upgrade-helper", "--json", "--version"]
    assert "--signature" in runtime.commands[0]
    assert "--sudoers" not in runtime.commands[0]
    assert runtime.commands[1][0:3] == ["upgrade-helper", "--json", "--version"]
    assert "--sudoers" in runtime.commands[1]


@pytest.mark.asyncio
async def test_current_helper_with_stale_policy_is_repaired_once() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stale = parse_status({**payload, "agent_version": HELPER_VERSION, "policy_version": "unknown"})
    runtime = FakeRuntime(parse_status(payload))

    result = await async_upgrade_if_needed(runtime, stale)

    assert result.policy_version == HELPER_VERSION
    assert len(runtime.commands) == 1
    assert "--sudoers" in runtime.commands[0]


@pytest.mark.asyncio
async def test_current_helper_is_not_replaced() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    current = parse_status(payload)
    runtime = FakeRuntime(current)
    assert await async_upgrade_if_needed(runtime, current) is current
    assert runtime.commands == []
