"""Constants for the Pi Manager integration."""

from __future__ import annotations

DOMAIN = "pi_manager"
INTEGRATION_VERSION = "0.2.6"
HELPER_VERSION = "0.2.5"
SCHEMA_VERSION = 1
MIN_POLL_INTERVAL = 15
DEFAULT_POLL_INTERVAL = 30
DEFAULT_PACKAGE_CHECK_CADENCE = 6 * 60 * 60
DEFAULT_TEMPERATURE_WARNING = 75.0

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DISPLAY_NAME = "display_name"
CONF_MACHINE_ID = "machine_id"
CONF_FINGERPRINT = "fingerprint"
CONF_KEY_ID = "key_id"
CONF_HELPER_VERSION = "helper_version"

CONF_POLL_INTERVAL = "poll_interval"
CONF_FILESYSTEM_INCLUDE = "filesystem_include"
CONF_FILESYSTEM_EXCLUDE = "filesystem_exclude"
CONF_NETWORK_INCLUDE = "network_include"
CONF_NETWORK_EXCLUDE = "network_exclude"
CONF_MONITORED_SERVICES = "monitored_services"
CONF_ENABLE_PACKAGE_CHECKS = "enable_package_checks"
CONF_PACKAGE_CHECK_CADENCE = "package_check_cadence"
CONF_TEMPERATURE_WARNING = "temperature_warning"
CONF_ENABLE_DANGEROUS_CONTROLS = "enable_dangerous_controls"

DEFAULT_OPTIONS: dict[str, object] = {
    CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
    CONF_FILESYSTEM_INCLUDE: [],
    CONF_FILESYSTEM_EXCLUDE: [],
    CONF_NETWORK_INCLUDE: [],
    CONF_NETWORK_EXCLUDE: [],
    CONF_MONITORED_SERVICES: [],
    CONF_ENABLE_PACKAGE_CHECKS: True,
    CONF_PACKAGE_CHECK_CADENCE: DEFAULT_PACKAGE_CHECK_CADENCE,
    CONF_TEMPERATURE_WARNING: DEFAULT_TEMPERATURE_WARNING,
    CONF_ENABLE_DANGEROUS_CONTROLS: False,
}

PLATFORMS: list[str] = ["sensor", "binary_sensor", "button"]

REMOTE_AGENT_PATH = "/usr/local/lib/pi-manager/pi_manager_agent.py"
REMOTE_CTL_PATH = "/usr/local/sbin/pi-managerctl"
REMOTE_SUDOERS_PATH = "/etc/sudoers.d/pi-manager"
REMOTE_CONFIG_PATH = "/etc/pi-manager/config.json"
REMOTE_TRUST_PATH = "/etc/pi-manager/trust.json"
REMOTE_STATE_PATH = "/var/lib/pi-manager/state.json"

MAX_COMMAND_OUTPUT = 256 * 1024
DEFAULT_COMMAND_TIMEOUT = 30.0
UPDATE_COMMAND_TIMEOUT = 15.0

SERVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9@_.:-]*\.service$"
TAILSCALE_SERVICE = "tailscaled.service"
