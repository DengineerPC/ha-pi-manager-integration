from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.pi_manager.const import DOMAIN
from custom_components.pi_manager.contract import parse_status
from custom_components.pi_manager.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redact_bootstrap_and_key_material() -> None:
    fixture = Path(__file__).parent / "fixtures" / "healthy_status.json"
    status = parse_status(json.loads(fixture.read_text(encoding="utf-8")))
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(data=status, last_update_success=True, last_error_category=None)
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            "host": "pi.example",
            "password": "bootstrap-secret",
            "key_id": "machine-key-reference",
            "fingerprint": "SHA256:private-trust-record",
            "private_key": "PRIVATE KEY CONTENT",
        },
        options={"poll_interval": 30},
    )
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: runtime}})

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    encoded = json.dumps(diagnostics)

    assert "bootstrap-secret" not in encoded
    assert "PRIVATE KEY CONTENT" not in encoded
    assert diagnostics["entry_data"]["password"] == "<redacted>"
    assert diagnostics["entry_data"]["key_id"] == "<redacted>"
    assert diagnostics["entry_data"]["fingerprint"] == "<redacted>"
