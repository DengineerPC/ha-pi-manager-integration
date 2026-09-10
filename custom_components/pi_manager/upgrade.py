"""Signed, idempotent remote helper upgrades."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any

from .const import HELPER_VERSION
from .errors import HelperIncompatibleError, PiManagerError
from .models import HostStatus

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


async def async_upgrade_if_needed(runtime: Any, status: HostStatus) -> HostStatus:
    """Upgrade an older helper once, preserving identity, trust, and key."""

    if _version_tuple(status.agent_version) >= _version_tuple(HELPER_VERSION):
        return status
    if runtime.helper_upgrade_attempted:
        return status
    runtime.helper_upgrade_attempted = True
    try:
        material = await runtime.key_store.async_load_material(runtime.entry.data["key_id"])
        helper_dir = Path(__file__).parent / "remote"
        agent_source, ctl_source = await asyncio.gather(
            asyncio.to_thread((helper_dir / "pi_manager_agent.py").read_bytes),
            asyncio.to_thread((helper_dir / "pi-managerctl").read_bytes),
        )
        signature_payload = HELPER_VERSION.encode("utf-8") + b"\0" + agent_source + b"\0" + ctl_source
        secret = base64.b64decode(material.upgrade_secret.encode("ascii"), validate=True)
        if len(secret) != 32:
            raise ValueError("upgrade trust secret has an invalid length")
        signature = hmac.new(
            secret,
            signature_payload,
            hashlib.sha256,
        ).hexdigest()
        await runtime.async_run_helper(
            [
                "upgrade-helper",
                "--json",
                "--version",
                HELPER_VERSION,
                "--agent",
                base64.b64encode(agent_source).decode("ascii"),
                "--ctl",
                base64.b64encode(ctl_source).decode("ascii"),
                "--signature",
                signature,
            ],
            serialized=True,
        )
        refreshed = await runtime.async_status()
        if _version_tuple(refreshed.agent_version) < _version_tuple(HELPER_VERSION):
            raise HelperIncompatibleError("The helper did not report the upgraded version")
        return refreshed
    except HelperIncompatibleError:
        raise
    except (OSError, PiManagerError, ValueError) as err:
        raise HelperIncompatibleError("The helper upgrade trust material is invalid") from err


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(value)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else (0, 0, 0)
