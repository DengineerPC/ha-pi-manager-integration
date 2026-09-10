from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys

import pytest

from custom_components.pi_manager.remote import pi_manager_agent as agent


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
