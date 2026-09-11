"""Render and validate the restricted Pi Manager sudoers policy."""

from __future__ import annotations

import re
from pathlib import Path

from .const import HELPER_VERSION
from .validation import validate_username

REMOTE_CTL = "/usr/local/sbin/pi-managerctl"
SUDOERS_COMMANDS: tuple[str, ...] = (
    f"{REMOTE_CTL} status --json",
    f"{REMOTE_CTL} check-updates --json",
    f"{REMOTE_CTL} update --json",
    f"{REMOTE_CTL} upgrade --json",
    f"{REMOTE_CTL} dist-upgrade --json",
    f"{REMOTE_CTL} preview-upgrade --json",
    f"{REMOTE_CTL} preview-dist-upgrade --json",
    f"{REMOTE_CTL} preview-autoremove --json",
    f"{REMOTE_CTL} audit-packages --json",
    f"{REMOTE_CTL} show-package-holds --json",
    f"{REMOTE_CTL} failed-services --json",
    f"{REMOTE_CTL} configure-packages --json",
    f"{REMOTE_CTL} repair-packages --json",
    f"{REMOTE_CTL} autoremove --json",
    f"{REMOTE_CTL} autoclean --json",
    f"{REMOTE_CTL} clean-cache --json",
    f"{REMOTE_CTL} reboot --json",
    f"{REMOTE_CTL} shutdown --json",
    f"{REMOTE_CTL} service-status *",
    f"{REMOTE_CTL} service-restart *",
    f"{REMOTE_CTL} service-validate *",
    f"{REMOTE_CTL} configure-services --json --services-json *",
    f"{REMOTE_CTL} configure-trust --json --secret *",
    f"{REMOTE_CTL} upgrade-helper --json --version * --agent * --ctl * --signature *",
    f"{REMOTE_CTL} upgrade-helper --json --version * --agent * --ctl * --signature * --sudoers *",
)

_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
_POLICY_PREFIX = " ALL=(root) NOPASSWD: "
_POLICY_MARKER = "# Pi Manager sudo policy version: "


def render_sudoers(template_path: Path, username: str) -> str:
    """Render and fail closed if the bundled policy is not exact."""

    normalized_username = validate_username(username)
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as err:
        raise ValueError("sudoers_source_unavailable") from err
    rendered = template.replace("{username}", normalized_username).replace("{helper_version}", HELPER_VERSION)
    validate_sudoers_policy(rendered, username=normalized_username, version=HELPER_VERSION)
    return rendered


def validate_sudoers_policy(content: str, *, username: str | None = None, version: str = HELPER_VERSION) -> None:
    """Require one exact, root-only Pi Manager policy line."""

    marker = f"{_POLICY_MARKER}{version}"
    comments = [line.strip() for line in content.splitlines() if line.strip().startswith("#")]
    if marker not in comments:
        raise ValueError("sudoers_policy_invalid")

    policy_lines = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(policy_lines) != 1:
        raise ValueError("sudoers_policy_invalid")
    policy_line = policy_lines[0]

    if username is None:
        policy_username, separator, command_text = policy_line.partition(_POLICY_PREFIX)
        if not separator or not _USERNAME_RE.fullmatch(policy_username):
            raise ValueError("sudoers_policy_invalid")
    else:
        normalized_username = validate_username(username)
        expected_prefix = f"{normalized_username}{_POLICY_PREFIX}"
        if not policy_line.startswith(expected_prefix):
            raise ValueError("sudoers_policy_invalid")
        command_text = policy_line[len(expected_prefix) :]

    if tuple(command_text.split(", ")) != SUDOERS_COMMANDS:
        raise ValueError("sudoers_policy_invalid")
