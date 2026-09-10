from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.pi_manager import actions
from custom_components.pi_manager.key_store import KeyStore


@pytest.mark.asyncio
async def test_two_hosts_keep_keys_and_refresh_targets_separate(tmp_path) -> None:
    store = KeyStore(tmp_path)
    first_key = await store.async_create_for_machine("a" * 32)
    second_key = await store.async_create_for_machine("b" * 32)

    first = SimpleNamespace(coordinator=SimpleNamespace(refreshes=0))
    second = SimpleNamespace(coordinator=SimpleNamespace(refreshes=0))

    async def refresh_first():
        first.coordinator.refreshes += 1

    async def refresh_second():
        second.coordinator.refreshes += 1

    first.coordinator.async_request_refresh = refresh_first
    second.coordinator.async_request_refresh = refresh_second
    await actions.async_refresh(first)

    assert first.coordinator.refreshes == 1
    assert second.coordinator.refreshes == 0
    assert first_key.key_id != second_key.key_id
