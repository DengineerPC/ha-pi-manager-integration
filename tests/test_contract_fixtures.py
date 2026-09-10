from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")))
def test_contract_fixtures_have_schema_version(fixture: Path) -> None:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1


def test_contract_fixture_without_schema_version_is_rejected() -> None:
    payload = {"machine": {"machine_id": "x"}}
    assert "schema_version" not in payload
