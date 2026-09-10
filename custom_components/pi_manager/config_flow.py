"""UI setup, trust preview, reconfigure, and reauthentication flows."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback

from .bootstrap import Bootstrapper
from .const import (
    CONF_DISPLAY_NAME,
    CONF_FINGERPRINT,
    CONF_HELPER_VERSION,
    CONF_KEY_ID,
    CONF_MACHINE_ID,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .discovery import discover_host
from .errors import (
    BootstrapError,
    HostDiscoveryError,
    HostFingerprintMismatch,
    PiManagerAuthenticationError,
    PiManagerConnectionError,
)
from .ssh import AsyncSSHClient
from .validation import validate_host, validate_port, validate_username

_LOGGER = logging.getLogger(__name__)


class PiManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Configure one Pi Manager host without persisting a password."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: AsyncSSHClient | None = None
        self._setup: dict[str, Any] = {}
        self._discovery: Any = None
        self._target_entry_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Collect credentials and discover the remote host."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
                port = validate_port(user_input[CONF_PORT])
                username = validate_username(user_input[CONF_USERNAME])
                password = user_input[CONF_PASSWORD]
                if not password:
                    raise PiManagerAuthenticationError("SSH authentication failed")
                self._client = AsyncSSHClient(host, port, username)
                await self._client.connect(password=password, allow_first_trust=True)
                self._discovery = await discover_host(self._client, bootstrap_password=password)
                if self._target_entry_id is None:
                    await self.async_set_unique_id(self._discovery.machine_id)
                if self._duplicate_machine(self._discovery.machine_id):
                    await self._client.close()
                    self._client = None
                    return self.async_abort(reason="already_configured")
                self._setup = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_DISPLAY_NAME: user_input.get(CONF_DISPLAY_NAME, self._existing_display_name()).strip(),
                }
                return await self.async_step_confirm()
            except (ValueError, PiManagerAuthenticationError) as err:
                errors["base"] = _error_key(err)
            except HostFingerprintMismatch:
                errors["base"] = "fingerprint_changed"
            except (HostDiscoveryError, PiManagerConnectionError) as err:
                errors["base"] = _error_key(err)
            except Exception as err:  # noqa: BLE001 - config-flow boundary maps unexpected setup failures
                _LOGGER.debug("Unexpected Pi Manager setup failure", exc_info=err)
                errors["base"] = "cannot_connect"
            await self._close_client()

        return self.async_show_form(step_id="user", data_schema=_user_schema(), errors=errors)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Show detected capabilities before installing the helper."""

        if self._client is None or self._discovery is None:
            return self.async_abort(reason="setup_expired")
        if user_input is not None:
            if not user_input.get("confirm", False):
                await self._close_client()
                return self.async_abort(reason="cancelled")
            try:
                bootstrapper = Bootstrapper(
                    config_dir=self.hass.config.config_dir,
                    client_factory=lambda host, port, username: AsyncSSHClient(host, port, username),
                )
                result = await bootstrapper.async_bootstrap(
                    self._client,
                    discovery=self._discovery,
                    username=self._setup[CONF_USERNAME],
                    password=self._setup[CONF_PASSWORD],
                    options=DEFAULT_OPTIONS,
                )
                title = self._setup[CONF_DISPLAY_NAME] or self._discovery.hostname
                data = {
                    CONF_HOST: self._setup[CONF_HOST],
                    CONF_PORT: self._setup[CONF_PORT],
                    CONF_USERNAME: self._setup[CONF_USERNAME],
                    CONF_DISPLAY_NAME: self._setup[CONF_DISPLAY_NAME],
                    CONF_MACHINE_ID: result.machine_id,
                    CONF_FINGERPRINT: result.fingerprint,
                    CONF_KEY_ID: result.key_id,
                    CONF_HELPER_VERSION: result.helper_version,
                }
                await self._close_client()
                if self._target_entry_id:
                    entry = self.hass.config_entries.async_get_entry(self._target_entry_id)
                    self._target_entry_id = None
                    if entry is not None:
                        return self.async_update_reload_and_abort(
                            entry,
                            data_updates=data,
                            title=title,
                        )
                return self.async_create_entry(title=title, data=data, options=dict(DEFAULT_OPTIONS))
            except BootstrapError as err:
                _LOGGER.debug("Pi Manager bootstrap failed at %s", err.stage)
                return self.async_show_form(
                    step_id="confirm",
                    data_schema=_confirm_schema(),
                    errors={"base": _error_key(err)},
                    description_placeholders=self._placeholders(),
                )
            except HostFingerprintMismatch:
                await self._close_client()
                return self.async_abort(reason="fingerprint_changed")

        return self.async_show_form(
            step_id="confirm",
            data_schema=_confirm_schema(),
            description_placeholders=self._placeholders(),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Re-run discovery/bootstrap for an existing entry without deleting it."""

        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="not_found")
        if user_input is not None:
            self._target_entry_id = self.context.get("entry_id")
            self._setup = dict(user_input)
            self._setup[CONF_DISPLAY_NAME] = entry.data.get(CONF_DISPLAY_NAME, "")
            return await self.async_step_user(user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_connection_schema(include_display_name=False))

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Allow credentials/trust to be repaired through a supported flow."""

        if user_input is not None:
            self._target_entry_id = self.context.get("entry_id")
            return await self.async_step_user(user_input)
        return self.async_show_form(step_id="reauth", data_schema=_connection_schema(include_display_name=False))

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        from .options_flow import PiManagerOptionsFlow

        return PiManagerOptionsFlow()

    def _duplicate_machine(self, machine_id: str) -> bool:
        return any(
            entry.entry_id != self._target_entry_id and entry.data.get(CONF_MACHINE_ID) == machine_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    def _existing_display_name(self) -> str:
        if not self._target_entry_id:
            return ""
        entry = self.hass.config_entries.async_get_entry(self._target_entry_id)
        return entry.data.get(CONF_DISPLAY_NAME, "") if entry is not None else ""

    def _placeholders(self) -> dict[str, str]:
        return {
            "hostname": self._discovery.hostname,
            "os": self._discovery.os_name,
            "architecture": self._discovery.architecture,
            "machine_id": f"{self._discovery.machine_id[:8]}…",
            "fingerprint": self._discovery.fingerprint,
            "sudo": "available" if self._discovery.sudo_available else "unavailable",
            "helper": "upgrade" if self._discovery.helper_installed else "install",
        }

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


def _connection_schema(*, include_display_name: bool) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=22): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
    if include_display_name:
        schema[vol.Optional(CONF_DISPLAY_NAME, default="")] = str
    return vol.Schema(schema)


def _user_schema() -> vol.Schema:
    return _connection_schema(include_display_name=True)


def _confirm_schema() -> vol.Schema:
    return vol.Schema({vol.Required("confirm", default=True): bool})


def _error_key(error: Exception) -> str:
    if isinstance(error, BootstrapError):
        return {
            "authentication": "wrong_password",
            "fingerprint": "fingerprint_changed",
            "target_read_only": "read_only_target",
            "sudoers_validation": "sudo_unavailable",
            "final_status": "bootstrap_partial",
        }.get(error.stage, "bootstrap_partial")
    if isinstance(error, HostDiscoveryError):
        return error.category
    if isinstance(error, PiManagerAuthenticationError):
        return "wrong_password"
    if isinstance(error, PiManagerConnectionError):
        return "cannot_connect"
    if isinstance(error, ValueError):
        return str(error)
    return "cannot_connect"
