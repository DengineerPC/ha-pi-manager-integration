from __future__ import annotations

from pathlib import Path

from custom_components.pi_manager.const import HELPER_VERSION
from custom_components.pi_manager.sudoers import SUDOERS_COMMANDS, render_sudoers

TEMPLATE = Path(__file__).parents[1] / "custom_components" / "pi_manager" / "remote" / "sudoers.template"
CTL = "/usr/local/sbin/pi-managerctl"
EXPECTED_COMMANDS = (
    f"{CTL} status --json",
    f"{CTL} check-updates --json",
    f"{CTL} update --json",
    f"{CTL} upgrade --json",
    f"{CTL} dist-upgrade --json",
    f"{CTL} preview-upgrade --json",
    f"{CTL} preview-dist-upgrade --json",
    f"{CTL} preview-autoremove --json",
    f"{CTL} audit-packages --json",
    f"{CTL} show-package-holds --json",
    f"{CTL} failed-services --json",
    f"{CTL} configure-packages --json",
    f"{CTL} repair-packages --json",
    f"{CTL} autoremove --json",
    f"{CTL} autoclean --json",
    f"{CTL} clean-cache --json",
    f"{CTL} reboot --json",
    f"{CTL} shutdown --json",
    f"{CTL} service-status *",
    f"{CTL} service-restart *",
    f"{CTL} service-validate *",
    f"{CTL} configure-services --json --services-json *",
    f"{CTL} configure-trust --json --secret *",
    f"{CTL} upgrade-helper --json --version * --agent * --ctl * --signature *",
    f"{CTL} upgrade-helper --json --version * --agent * --ctl * --signature * --sudoers *",
)


def _policy_commands(rendered: str) -> tuple[str, ...]:
    lines = [line.strip() for line in rendered.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert len(lines) == 1
    prefix = "denis ALL=(root) NOPASSWD: "
    assert lines[0].startswith(prefix)
    return tuple(lines[0][len(prefix) :].split(", "))


def test_rendered_policy_covers_every_explicit_button_operation() -> None:
    rendered = render_sudoers(TEMPLATE, "denis")

    assert _policy_commands(rendered) == EXPECTED_COMMANDS
    assert SUDOERS_COMMANDS == EXPECTED_COMMANDS
    assert f"# Pi Manager sudo policy version: {HELPER_VERSION}" in rendered
    assert "{username}" not in rendered
    assert "{helper_version}" not in rendered
    assert "job-worker" not in rendered
    assert "NOPASSWD: ALL" not in rendered
    assert "ALL=(ALL)" not in rendered
    assert "/bin/sh" not in rendered
    assert "/bin/bash" not in rendered
    assert "/usr/bin/apt" not in rendered
    assert "/bin/systemctl" not in rendered


def test_rendered_policy_rejects_invalid_username() -> None:
    import pytest

    with pytest.raises(ValueError, match="username_invalid"):
        render_sudoers(TEMPLATE, "denis;reboot")
