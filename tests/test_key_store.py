from __future__ import annotations

import os
import stat

import pytest

from custom_components.pi_manager.key_store import KeyStore


@pytest.mark.asyncio
async def test_each_machine_gets_independent_key_material(tmp_path) -> None:
    store = KeyStore(tmp_path)
    first = await store.async_create_for_machine("a" * 32)
    second = await store.async_create_for_machine("b" * 32)

    assert first.key_id != second.key_id
    assert first.private_key != second.private_key
    assert first.public_key != second.public_key
    assert first.upgrade_secret != second.upgrade_secret
    assert store.root == tmp_path / "pi_manager_keys"
    assert not (tmp_path / ".storage").exists()

    if os.name != "nt":
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
        assert stat.S_IMODE((store.root / f"{'a' * 32}.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((store.root / f"{'a' * 32}.hmac").stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_existing_keypair_is_reused_idempotently(tmp_path) -> None:
    store = KeyStore(tmp_path)
    first = await store.async_create_for_machine("c" * 32)
    second = await store.async_create_for_machine("c" * 32)

    assert second == first
