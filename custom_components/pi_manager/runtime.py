"""Per-config-entry runtime state and action boundary."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_ENABLE_PACKAGE_CHECKS,
    CONF_FINGERPRINT,
    CONF_HOST,
    CONF_KEY_ID,
    CONF_PACKAGE_CHECK_CADENCE,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_COMMAND_TIMEOUT,
    REMOTE_CTL_PATH,
)
from .contract import parse_json_response, parse_status, parse_update_response
from .errors import HelperProtocolError, PiManagerConnectionError
from .key_store import KeyStore
from .models import CommandResult, HostStatus, UpdateInfo
from .ssh import AsyncSSHClient, SSHClientProtocol
from .validation import validate_service_name


@dataclass(slots=True)
class PiManagerRuntime:
    """Own all mutable connection state for exactly one config entry."""

    hass: Any
    entry: Any
    key_store: KeyStore
    client: SSHClientProtocol | None = None
    coordinator: Any = None
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    helper_upgrade_attempted: bool = False
    package_check_attempted_at: float | None = None

    @property
    def options(self) -> Mapping[str, Any]:
        return dict(self.entry.options)

    async def async_ensure_connected(self) -> SSHClientProtocol:
        if self.client is not None:
            return self.client
        key = await self.key_store.async_load(self.entry.data[CONF_KEY_ID])
        client = AsyncSSHClient(
            self.entry.data[CONF_HOST],
            int(self.entry.data[CONF_PORT]),
            self.entry.data[CONF_USERNAME],
        )
        try:
            await client.connect(private_key=key, expected_fingerprint=self.entry.data[CONF_FINGERPRINT])
        except Exception:
            await client.close()
            raise
        self.client = client
        return client

    async def async_run_helper(
        self,
        args: Sequence[str],
        *,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
        serialized: bool = False,
    ) -> CommandResult:
        """Run an explicit helper operation for this entry only."""

        lock = self.action_lock if serialized else _NullAsyncLock()
        async with lock:
            client = await self.async_ensure_connected()
            try:
                result = await client.run_sudo_nopass([REMOTE_CTL_PATH, *args], timeout=timeout)
            except PiManagerConnectionError:
                await self._drop_client()
                raise
            if not result.ok:
                error_code = _error_code(result.stdout)
                if error_code:
                    raise HelperProtocolError(error_code)
                raise HelperProtocolError("helper_command_failed")
            return result

    async def async_status(self) -> HostStatus:
        result = await self.async_run_helper(["status", "--json"])
        status = parse_status(parse_json_response(result.stdout))
        if status.machine.machine_id != self.entry.data.get("machine_id"):
            raise HelperProtocolError("machine_identity_changed")
        return status

    async def async_check_updates(self) -> UpdateInfo:
        result = await self.async_run_helper(["check-updates", "--json"], timeout=360, serialized=True)
        updates = parse_update_response(parse_json_response(result.stdout))
        self.package_check_attempted_at = asyncio.get_running_loop().time()
        return updates

    async def async_check_updates_if_due(self) -> UpdateInfo | None:
        """Run the optional apt check at its configured cadence."""

        if not self.options.get(CONF_ENABLE_PACKAGE_CHECKS, True):
            return None
        now = asyncio.get_running_loop().time()
        try:
            cadence = max(900, int(self.options.get(CONF_PACKAGE_CHECK_CADENCE, 6 * 60 * 60)))
        except (TypeError, ValueError):
            cadence = 6 * 60 * 60
        if self.package_check_attempted_at is not None and now - self.package_check_attempted_at < cadence:
            return None
        self.package_check_attempted_at = now
        return await self.async_check_updates()

    async def async_start_update(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("update")

    async def async_start_package_job(self, operation: str) -> Mapping[str, Any]:
        result = await self.async_run_helper([operation, "--json"], serialized=True)
        return parse_json_response(result.stdout)

    async def async_preview_upgrade(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("preview-upgrade")

    async def async_preview_dist_upgrade(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("preview-dist-upgrade")

    async def async_preview_autoremove(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("preview-autoremove")

    async def async_audit_packages(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("audit-packages")

    async def async_show_package_holds(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("show-package-holds")

    async def async_failed_services(self) -> Mapping[str, Any]:
        return await self._async_maintenance_query("failed-services")

    async def _async_maintenance_query(self, operation: str) -> Mapping[str, Any]:
        result = await self.async_run_helper([operation, "--json"], timeout=360, serialized=True)
        return parse_json_response(result.stdout)

    async def async_start_dist_upgrade(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("dist-upgrade")

    async def async_start_configure_packages(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("configure-packages")

    async def async_start_repair_packages(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("repair-packages")

    async def async_start_autoremove(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("autoremove")

    async def async_start_autoclean(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("autoclean")

    async def async_start_clean_cache(self) -> Mapping[str, Any]:
        return await self.async_start_package_job("clean-cache")

    async def async_reboot(self) -> Mapping[str, Any]:
        result = await self.async_run_helper(["reboot", "--json"], serialized=True)
        return parse_json_response(result.stdout)

    async def async_shutdown(self) -> Mapping[str, Any]:
        result = await self.async_run_helper(["shutdown", "--json"], serialized=True)
        return parse_json_response(result.stdout)

    async def async_validate_service(self, name: str) -> Mapping[str, Any]:
        validate_service_name(name)
        result = await self.async_run_helper(["service-validate", name, "--json"], serialized=True)
        return parse_json_response(result.stdout)

    async def async_restart_service(self, name: str) -> Mapping[str, Any]:
        validate_service_name(name)
        result = await self.async_run_helper(["service-restart", name, "--json"], serialized=True)
        return parse_json_response(result.stdout)

    async def async_configure_services(
        self, services: Sequence[str], options: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        options = options or {}
        configuration = {
            "services": list(services),
            "filesystem_include": list(options.get("filesystem_include", [])),
            "filesystem_exclude": list(options.get("filesystem_exclude", [])),
            "network_include": list(options.get("network_include", [])),
            "network_exclude": list(options.get("network_exclude", [])),
        }
        encoded = base64.b64encode(json.dumps(configuration).encode("utf-8")).decode("ascii")
        result = await self.async_run_helper(
            ["configure-services", "--json", "--services-json", encoded], serialized=True
        )
        return parse_json_response(result.stdout)

    async def _drop_client(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            await client.close()

    async def async_close(self) -> None:
        await self._drop_client()


class _NullAsyncLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback


def _error_code(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None
