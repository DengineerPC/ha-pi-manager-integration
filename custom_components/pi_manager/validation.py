"""Input validation shared by config flows and the remote helper boundary."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection

from .const import SERVICE_NAME_PATTERN
from .errors import ServiceNameError

_SERVICE_RE = re.compile(SERVICE_NAME_PATTERN)
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


def validate_host(host: str) -> str:
    """Validate a hostname or IP literal without resolving it."""

    value = host.strip()
    if not value or len(value) > 253 or any(ord(char) < 32 for char in value):
        raise ValueError("host_invalid")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(value) or ".." in value:
            raise ValueError("host_invalid") from None
    return value


def validate_port(port: int | str) -> int:
    """Validate a TCP port."""

    try:
        value = int(port)
    except (TypeError, ValueError) as err:
        raise ValueError("port_invalid") from err
    if not 1 <= value <= 65535:
        raise ValueError("port_invalid")
    return value


def validate_username(username: str) -> str:
    """Reject empty or control-character usernames."""

    value = username.strip()
    if not _USERNAME_RE.fullmatch(value):
        raise ValueError("username_invalid")
    return value


def validate_service_name(name: str) -> str:
    """Validate a systemd service unit name before it reaches SSH."""

    value = name.strip()
    if value != name or not _SERVICE_RE.fullmatch(value):
        raise ServiceNameError("service_invalid")
    if any(char in value for char in ";|&$`()<>/\\\n\r\t"):
        raise ServiceNameError("service_invalid")
    return value


def validate_service_allowlist(name: str, allowlist: Collection[str]) -> str:
    """Validate syntax and require membership in the configured allowlist."""

    value = validate_service_name(name)
    if value not in allowlist:
        raise ServiceNameError("service_not_allowlisted")
    return value


def validate_service_list(names: Collection[str]) -> tuple[str, ...]:
    """Validate and de-duplicate a configured service list."""

    result: list[str] = []
    for name in names:
        validated = validate_service_name(name)
        if validated not in result:
            result.append(validated)
    return tuple(result)
