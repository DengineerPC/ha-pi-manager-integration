from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.pi_manager.contract import parse_json_response, parse_status, parse_update_response
from custom_components.pi_manager.errors import HelperProtocolError

FIXTURE = Path(__file__).parent / "fixtures" / "healthy_status.json"


def test_parse_healthy_status() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    status = parse_status(payload)
    assert status.machine.machine_id == "0123456789abcdef0123456789abcdef"
    assert status.filesystems[1].mount == "/mnt/extstorage"
    assert status.updates.available == 7
    assert status.services[0].name == "smbd.service"


def test_parse_optional_maintenance_state() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["maintenance"] = {
        "previews": [
            {
                "operation": "preview-dist-upgrade",
                "last_checked": "2026-09-10T00:00:00Z",
                "upgraded": 4,
                "newly_installed": 1,
                "to_remove": 2,
                "not_upgraded": 3,
                "reboot_required": True,
                "summary": "4 upgrades, 1 new packages, 2 removals, 3 held back",
            }
        ],
        "audit": {
            "last_checked": "2026-09-10T00:00:00Z",
            "healthy": False,
            "dpkg_issues": 1,
            "apt_healthy": True,
            "summary": "Package database needs attention",
        },
        "holds": {"last_checked": "2026-09-10T00:00:00Z", "packages": ["linux-image-arm64"]},
        "failed_services": {"last_checked": "2026-09-10T00:00:00Z", "services": ["smbd.service"]},
    }
    status = parse_status(payload)

    assert status.maintenance.previews[0].to_remove == 2
    assert status.maintenance.audit is not None
    assert status.maintenance.audit.healthy is False
    assert status.maintenance.holds is not None
    assert status.maintenance.holds.packages == ("linux-image-arm64",)
    assert status.maintenance.failed_services is not None
    assert status.maintenance.failed_services.services == ("smbd.service",)


def test_parse_old_status_defaults_optional_maintenance() -> None:
    status = parse_status(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert status.maintenance.previews == ()
    assert status.maintenance.audit is None


def test_parse_json_rejects_malformed_response() -> None:
    with pytest.raises(HelperProtocolError, match="invalid_json"):
        parse_json_response("not json")


def test_parse_status_rejects_unsupported_schema() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    with pytest.raises(HelperProtocolError, match="schema_unsupported"):
        parse_status(payload)


def test_parse_status_rejects_unbounded_output() -> None:
    with pytest.raises(HelperProtocolError, match="too_large"):
        parse_json_response("{" + "x" * 300_000 + "}")


def test_parse_status_rejects_wrong_collection_shape() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["filesystems"] = {"mount": "/"}
    with pytest.raises(HelperProtocolError, match="filesystems_invalid"):
        parse_status(payload)


def test_parse_status_rejects_invalid_machine_identity() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["machine"]["machine_id"] = "not-a-machine-id"
    with pytest.raises(HelperProtocolError, match="machine_id_invalid"):
        parse_status(payload)


def test_parse_update_response_requires_schema_and_preserves_update_fields() -> None:
    payload = {"schema_version": 1, "updates": {"last_checked": "now", "available": 3, "security": 1}}
    updates = parse_update_response(payload)
    assert updates.available == 3
    assert updates.security == 1
    assert updates.reboot_required is False
