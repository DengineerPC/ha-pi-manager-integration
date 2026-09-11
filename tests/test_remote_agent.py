from __future__ import annotations

import base64
import hashlib
import hmac
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from custom_components.pi_manager.const import HELPER_VERSION
from custom_components.pi_manager.remote import pi_manager_agent as agent
from custom_components.pi_manager.sudoers import render_sudoers

SUDOERS_TEMPLATE = Path(__file__).parents[1] / "custom_components" / "pi_manager" / "remote" / "sudoers.template"


def _patch_status_dependencies(monkeypatch, tmp_path, services: list[str] | None = None) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"services": services or []}))
    monkeypatch.setattr(agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(agent, "CONFIG_PATH", config_path)
    monkeypatch.setattr(agent, "_machine", lambda: {"machine_id": "a" * 32})
    monkeypatch.setattr(
        agent,
        "_cpu",
        lambda state: {
            "usage_percent": 1.0,
            "temperature_c": None,
            "load_1": 0,
            "load_5": 0,
            "load_15": 0,
            "_state": {},
        },
    )
    monkeypatch.setattr(agent, "_memory", lambda: {"total_bytes": 1, "used_bytes": 0, "used_percent": 0})
    monkeypatch.setattr(agent, "_filesystems", lambda config: [])
    monkeypatch.setattr(agent, "_network", lambda config: [])
    monkeypatch.setattr(agent, "_job_state", lambda state: None)


def test_service_injection_is_rejected_by_helper() -> None:
    for name in (
        "smbd;reboot",
        "../../something.service",
        "$(reboot).service",
        "smbd.service && reboot",
    ):
        assert not agent._service_name_valid(name)
    assert agent._service_name_valid("smbd.service")


def test_configure_services_accepts_only_valid_encoded_list(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(agent, "CONFIG_PATH", config_path)
    encoded = base64.b64encode(
        json.dumps({"services": ["smbd.service"], "filesystem_exclude": ["/tmp"]}).encode()
    ).decode()
    result = agent._configure_services(encoded)
    assert result["configured_services"] == ["smbd.service"]
    assert result["filesystem_exclude"] == ["/tmp"]
    assert json.loads(config_path.read_text())["services"] == ["smbd.service"]

    invalid = base64.b64encode(json.dumps(["smbd;reboot"]).encode()).decode()
    with pytest.raises(agent.AgentError, match="service_invalid"):
        agent._configure_services(invalid)


def test_start_update_rejects_concurrent_job(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"job": {"state": "running", "id": "existing"}}))
    monkeypatch.setattr(agent, "STATE_PATH", state_path)
    monkeypatch.setattr(agent, "UPDATE_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(agent, "_job_state", lambda state: state["job"])
    with pytest.raises(agent.AgentError, match="update_already_running"):
        agent._start_update()


def test_package_job_commands_are_fixed_and_worker_is_not_a_sudo_operation() -> None:
    assert agent.PACKAGE_JOB_COMMANDS["dist-upgrade"] == ("/usr/bin/apt-get", "-y", "dist-upgrade")
    assert agent.PACKAGE_JOB_COMMANDS["repair-packages"] == ("/usr/bin/apt-get", "-f", "-y", "install")
    assert agent._parse_args(["dist-upgrade", "--json"]).operation == "dist-upgrade"
    assert agent._parse_args(["job-worker", "upgrade"]).operation == "job-worker"
    with pytest.raises(SystemExit):
        agent._parse_args(["upgrade", "--json", "--", "/bin/sh"])


def test_start_package_job_uses_fixed_root_worker(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(agent, "STATE_PATH", state_path)
    monkeypatch.setattr(agent, "UPDATE_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(agent, "_job_state", lambda state: None)
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent._start_package_job("dist-upgrade")

    assert result["state"] == "running"
    assert result["type"] == "dist-upgrade"
    assert calls[0][-3:] == [agent.REMOTE_CTL_PATH, "job-worker", "dist-upgrade"]
    assert "/usr/bin/apt-get" not in calls[0]


def test_package_preview_is_bounded_and_retained(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        agent,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            list(args),
            0,
            "2 upgraded, 1 newly installed, 3 to remove and 4 not upgraded.\n",
            "",
        ),
    )

    preview = agent._preview_package("preview-dist-upgrade")
    state = json.loads((tmp_path / "state.json").read_text())

    assert preview["upgraded"] == 2
    assert preview["newly_installed"] == 1
    assert preview["to_remove"] == 3
    assert preview["not_upgraded"] == 4
    assert state["maintenance"]["previews"]["preview-dist-upgrade"] == preview


def test_job_worker_requires_root_and_runs_only_selected_job(monkeypatch) -> None:
    monkeypatch.setattr(agent.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(agent.AgentError, match="job_worker_requires_root"):
        agent._run_package_job("upgrade")

    monkeypatch.setattr(agent.os, "geteuid", lambda: 0, raising=False)
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    assert agent._run_package_job("configure-packages") == 0
    assert calls == [["/usr/bin/dpkg", "--configure", "-a"]]


def test_status_response_always_contains_schema_version(monkeypatch, tmp_path) -> None:
    _patch_status_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(agent, "_optional_service_status", lambda: None)
    payload = agent._status()
    assert payload["agent_version"] == agent.AGENT_VERSION
    assert agent.SCHEMA_VERSION == 1


def test_optional_tailscale_probe_parses_loaded_unit(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(list(args))
        return subprocess.CompletedProcess(
            list(args),
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\n",
            "",
        )

    monkeypatch.setattr(agent, "_run", fake_run)

    assert agent._optional_service_status() == {
        "name": "tailscaled.service",
        "active": True,
        "state": "active",
        "substate": "running",
    }
    assert calls == [
        [
            "/bin/systemctl",
            "show",
            "tailscaled.service",
            "--property=LoadState,ActiveState,SubState",
            "--no-page",
        ]
    ]


def test_status_adds_loaded_tailscaled_service(monkeypatch, tmp_path) -> None:
    _patch_status_dependencies(monkeypatch, tmp_path)
    tailscale = {
        "name": "tailscaled.service",
        "active": True,
        "state": "active",
        "substate": "running",
    }
    monkeypatch.setattr(agent, "_optional_service_status", lambda: tailscale)

    payload = agent._status()

    assert [item for item in payload["services"] if item["name"] == "tailscaled.service"] == [tailscale]


def test_status_omits_missing_tailscaled_service(monkeypatch, tmp_path) -> None:
    _patch_status_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(agent, "_optional_service_status", lambda: None)

    payload = agent._status()

    assert all(item["name"] != "tailscaled.service" for item in payload["services"])


def test_status_does_not_duplicate_configured_tailscaled_service(monkeypatch, tmp_path) -> None:
    _patch_status_dependencies(monkeypatch, tmp_path, ["tailscaled.service"])
    configured = {
        "name": "tailscaled.service",
        "active": True,
        "state": "active",
        "substate": "running",
    }
    monkeypatch.setattr(agent, "_service_status", lambda name, config: configured)
    monkeypatch.setattr(agent, "_optional_service_status", lambda: configured)

    payload = agent._status()

    assert [item for item in payload["services"] if item["name"] == "tailscaled.service"] == [configured]


def test_helper_subprocess_output_is_bounded() -> None:
    result = agent._run([sys.executable, "-c", "print('ok')"])
    assert result.stdout.strip() == "ok"
    with pytest.raises(agent.AgentError, match="command_output_too_large"):
        agent._run([sys.executable, "-c", "print('x' * 300000)"])


def test_helper_upgrade_requires_per_host_signature(tmp_path, monkeypatch) -> None:
    trust_path = tmp_path / "trust.json"
    monkeypatch.setattr(agent, "TRUST_PATH", trust_path)
    secret = b"x" * 32
    encoded_secret = base64.b64encode(secret).decode()
    assert agent._configure_trust(encoded_secret) == {"configured": True}
    assert agent._configure_trust(encoded_secret) == {"configured": True, "unchanged": True}
    with pytest.raises(agent.AgentError, match="trust_already_configured"):
        agent._configure_trust(base64.b64encode(b"y" * 32).decode())
    agent_source = b'AGENT_VERSION = "0.2.0"\n'
    ctl_source = b"#!/usr/bin/python3\n"
    encoded_agent = base64.b64encode(agent_source).decode()
    encoded_ctl = base64.b64encode(ctl_source).decode()
    message = b"0.2.0\0" + agent_source + b"\0" + ctl_source
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    monkeypatch.setattr(agent, "REMOTE_AGENT_PATH", str(tmp_path / "agent.py"))
    monkeypatch.setattr(agent, "REMOTE_CTL_PATH", str(tmp_path / "ctl"))
    result = agent._upgrade_helper("0.2.0", encoded_agent, encoded_ctl, signature)
    assert result["upgraded"] is True
    with pytest.raises(agent.AgentError, match="signature_invalid"):
        agent._upgrade_helper("0.2.0", encoded_agent, encoded_ctl, "0" * 64)


def test_upgrade_helper_accepts_optional_signed_sudoers_payload() -> None:
    args = agent._parse_args(
        [
            "upgrade-helper",
            "--json",
            "--version",
            HELPER_VERSION,
            "--agent",
            "agent",
            "--ctl",
            "ctl",
            "--signature",
            "signature",
            "--sudoers",
            "policy",
        ]
    )

    assert args.sudoers == "policy"


def test_helper_upgrade_installs_valid_signed_sudoers_policy(tmp_path, monkeypatch) -> None:
    trust_path = tmp_path / "trust.json"
    sudoers_path = tmp_path / "sudoers"
    agent_path = tmp_path / "agent.py"
    ctl_path = tmp_path / "ctl"
    monkeypatch.setattr(agent, "TRUST_PATH", trust_path)
    monkeypatch.setattr(agent, "REMOTE_SUDOERS_PATH", str(sudoers_path))
    monkeypatch.setattr(agent, "REMOTE_AGENT_PATH", str(agent_path))
    monkeypatch.setattr(agent, "REMOTE_CTL_PATH", str(ctl_path))
    monkeypatch.setattr(agent.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(agent, "_set_root_ownership", lambda path: None)

    secret = b"x" * 32
    encoded_secret = base64.b64encode(secret).decode()
    assert agent._configure_trust(encoded_secret) == {"configured": True}
    agent_source = f'AGENT_VERSION = "{HELPER_VERSION}"\n'.encode()
    ctl_source = b"#!/usr/bin/python3\n"
    sudoers_source = render_sudoers(SUDOERS_TEMPLATE, "denis").encode()
    encoded_agent = base64.b64encode(agent_source).decode()
    encoded_ctl = base64.b64encode(ctl_source).decode()
    encoded_sudoers = base64.b64encode(sudoers_source).decode()
    message = HELPER_VERSION.encode() + b"\0" + agent_source + b"\0" + ctl_source + b"\0" + sudoers_source
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

    def fake_run(args, **kwargs):
        del kwargs
        assert list(args[:2]) == ["/usr/sbin/visudo", "-cf"]
        temporary_policy = Path(args[2])
        assert temporary_policy.parent == sudoers_path.parent
        assert temporary_policy != sudoers_path
        assert temporary_policy.read_bytes() == sudoers_source
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(agent, "_run", fake_run)
    result = agent._upgrade_helper(
        HELPER_VERSION,
        encoded_agent,
        encoded_ctl,
        signature,
        encoded_sudoers,
    )

    assert result["upgraded"] is True
    assert result["policy_version"] == HELPER_VERSION
    assert sudoers_path.read_bytes() == sudoers_source
    mode = stat.S_IMODE(sudoers_path.stat().st_mode)
    assert mode == 0o440 if sys.platform != "win32" else mode & 0o222 == 0
    assert agent_path.read_bytes() == agent_source
    assert ctl_path.read_bytes() == ctl_source


def test_invalid_sudoers_policy_is_rejected_before_helper_activation(tmp_path, monkeypatch) -> None:
    trust_path = tmp_path / "trust.json"
    agent_path = tmp_path / "agent.py"
    ctl_path = tmp_path / "ctl"
    sudoers_path = tmp_path / "sudoers"
    monkeypatch.setattr(agent, "TRUST_PATH", trust_path)
    monkeypatch.setattr(agent, "REMOTE_AGENT_PATH", str(agent_path))
    monkeypatch.setattr(agent, "REMOTE_CTL_PATH", str(ctl_path))
    monkeypatch.setattr(agent, "REMOTE_SUDOERS_PATH", str(sudoers_path))
    secret = b"y" * 32
    encoded_secret = base64.b64encode(secret).decode()
    agent._configure_trust(encoded_secret)
    agent_source = f'AGENT_VERSION = "{HELPER_VERSION}"\n'.encode()
    ctl_source = b"#!/usr/bin/python3\n"
    bad_policy = render_sudoers(SUDOERS_TEMPLATE, "denis").replace("ALL=(root)", "ALL=(ALL)").encode()
    message = HELPER_VERSION.encode() + b"\0" + agent_source + b"\0" + ctl_source + b"\0" + bad_policy
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

    with pytest.raises(agent.AgentError, match="sudoers_policy_invalid"):
        agent._upgrade_helper(
            HELPER_VERSION,
            base64.b64encode(agent_source).decode(),
            base64.b64encode(ctl_source).decode(),
            signature,
            base64.b64encode(bad_policy).decode(),
        )

    assert not agent_path.exists()
    assert not ctl_path.exists()
    assert not sudoers_path.exists()


def test_failed_sudoers_validation_preserves_active_policy(tmp_path, monkeypatch) -> None:
    trust_path = tmp_path / "trust.json"
    sudoers_path = tmp_path / "sudoers"
    agent_path = tmp_path / "agent.py"
    ctl_path = tmp_path / "ctl"
    old_policy = "existing policy\n"
    sudoers_path.write_text(old_policy, encoding="utf-8")
    monkeypatch.setattr(agent, "TRUST_PATH", trust_path)
    monkeypatch.setattr(agent, "REMOTE_SUDOERS_PATH", str(sudoers_path))
    monkeypatch.setattr(agent, "REMOTE_AGENT_PATH", str(agent_path))
    monkeypatch.setattr(agent, "REMOTE_CTL_PATH", str(ctl_path))
    monkeypatch.setattr(agent.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(agent, "_set_root_ownership", lambda path: None)
    secret = b"z" * 32
    encoded_secret = base64.b64encode(secret).decode()
    agent._configure_trust(encoded_secret)
    agent_source = f'AGENT_VERSION = "{HELPER_VERSION}"\n'.encode()
    ctl_source = b"#!/usr/bin/python3\n"
    sudoers_source = render_sudoers(SUDOERS_TEMPLATE, "denis").encode()
    message = HELPER_VERSION.encode() + b"\0" + agent_source + b"\0" + ctl_source + b"\0" + sudoers_source
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

    def failed_visudo(args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(list(args), 1, "", "invalid")

    monkeypatch.setattr(agent, "_run", failed_visudo)
    with pytest.raises(agent.AgentError, match="sudoers_validation_failed"):
        agent._upgrade_helper(
            HELPER_VERSION,
            base64.b64encode(agent_source).decode(),
            base64.b64encode(ctl_source).decode(),
            signature,
            base64.b64encode(sudoers_source).decode(),
        )

    assert sudoers_path.read_text(encoding="utf-8") == old_policy


def test_status_reports_current_validated_policy_version(monkeypatch, tmp_path) -> None:
    _patch_status_dependencies(monkeypatch, tmp_path)
    policy_path = tmp_path / "sudoers"
    policy_path.write_text(render_sudoers(SUDOERS_TEMPLATE, "denis"), encoding="utf-8")
    monkeypatch.setattr(agent, "REMOTE_SUDOERS_PATH", str(policy_path))
    monkeypatch.setattr(agent, "_optional_service_status", lambda: None)

    payload = agent._status()

    assert payload["policy_version"] == HELPER_VERSION
