"""Idempotent password-to-key bootstrap for a managed host."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .const import (
    CONF_FILESYSTEM_EXCLUDE,
    CONF_FILESYSTEM_INCLUDE,
    CONF_MONITORED_SERVICES,
    CONF_NETWORK_EXCLUDE,
    CONF_NETWORK_INCLUDE,
    REMOTE_AGENT_PATH,
    REMOTE_CTL_PATH,
    REMOTE_SUDOERS_PATH,
)
from .contract import parse_json_response, parse_status
from .errors import BootstrapError, HostFingerprintMismatch
from .key_store import KeyStore
from .models import BootstrapResult, HostDiscovery
from .ssh import SSHClientProtocol
from .validation import validate_service_list, validate_username

ClientFactory = Callable[[str, int, str], SSHClientProtocol | Any]


class Bootstrapper:
    """Deploy helper artifacts while retaining a deterministic retry state."""

    def __init__(
        self,
        *,
        config_dir: str | Path,
        client_factory: ClientFactory,
        helper_dir: str | Path | None = None,
    ) -> None:
        self.key_store = KeyStore(config_dir)
        self.client_factory = client_factory
        self.helper_dir = Path(helper_dir) if helper_dir else Path(__file__).parent / "remote"

    async def async_bootstrap(
        self,
        client: SSHClientProtocol,
        *,
        discovery: HostDiscovery,
        username: str,
        password: str,
        options: Mapping[str, Any] | None = None,
    ) -> BootstrapResult:
        """Run all bootstrap phases; never return until key-based status works."""

        validate_username(username)
        if not password:
            raise BootstrapError("authentication", "A bootstrap password is required")
        if not discovery.target_writable:
            raise BootstrapError("target_read_only", "The helper destination is not writable")
        if not discovery.fingerprint:
            raise BootstrapError("fingerprint", "The SSH fingerprint is unavailable")
        options = options or {}
        services = validate_service_list(options.get(CONF_MONITORED_SERVICES, []))
        key = await self.key_store.async_create_for_machine(discovery.machine_id)
        temporary_paths: list[str] = []
        changed_files: list[str] = []
        try:
            await self._append_authorized_key(client, key.public_key)
            changed_files.append("~/.ssh/authorized_keys")
            await self._install_helper(
                client, password, temporary_paths, "pi_manager_agent.py", REMOTE_AGENT_PATH, 0o644
            )
            changed_files.append(REMOTE_AGENT_PATH)
            await self._install_helper(client, password, temporary_paths, "pi-managerctl", REMOTE_CTL_PATH, 0o755)
            changed_files.append(REMOTE_CTL_PATH)
            sudoers = await asyncio.to_thread(self._render_sudoers, username)
            await self._install_sudoers(client, password, temporary_paths, sudoers)
            changed_files.append(REMOTE_SUDOERS_PATH)

            final_client = await self._new_client(client.host, client.port, client.username)
            try:
                await final_client.connect(private_key=key.private_key, expected_fingerprint=discovery.fingerprint)
                await self._configure_remote(final_client, key, services, options)
                status_result = await final_client.run_sudo_nopass([REMOTE_CTL_PATH, "status", "--json"])
                if not status_result.ok:
                    raise BootstrapError("final_status", "Key-based helper status verification failed")
                status = parse_status(parse_json_response(status_result.stdout))
                if status.machine.machine_id != discovery.machine_id:
                    raise BootstrapError("machine_identity", "The final helper identity changed during bootstrap")
            finally:
                await final_client.close()
        except BootstrapError, HostFingerprintMismatch:
            raise
        except Exception as err:
            raise BootstrapError("deployment", "Pi Manager bootstrap did not complete") from err
        finally:
            for path in temporary_paths:
                try:
                    await client.run(["/bin/rm", "-f", path])
                except Exception:  # noqa: BLE001 - cleanup must not hide the bootstrap result
                    pass
        return BootstrapResult(
            machine_id=discovery.machine_id,
            key_id=key.key_id,
            fingerprint=discovery.fingerprint,
            helper_version=await asyncio.to_thread(_helper_version, self.helper_dir / "pi_manager_agent.py"),
            changed_files=tuple(changed_files),
        )

    async def _new_client(self, host: str, port: int, username: str) -> SSHClientProtocol:
        client = self.client_factory(host, port, username)
        if inspect.isawaitable(client):
            client = await client
        return client

    async def _append_authorized_key(self, client: SSHClientProtocol, public_key: str) -> None:
        encoded = base64.b64encode(public_key.strip().encode("utf-8")).decode("ascii")
        script = (
            "import base64,os,pathlib,sys\n"
            "key=base64.b64decode(sys.argv[1]).decode('utf-8').strip()\n"
            "ssh=pathlib.Path.home()/'.ssh'\n"
            "ssh.mkdir(mode=0o700,exist_ok=True)\n"
            "os.chmod(ssh,0o700)\n"
            "target=ssh/'authorized_keys'\n"
            "if target.is_symlink(): raise SystemExit(7)\n"
            "old=target.read_text(encoding='utf-8') if target.exists() else ''\n"
            "lines=old.splitlines()\n"
            "if key not in lines:\n"
            "  separator='' if not old or old.endswith('\\n') else '\\n'\n"
            "  with target.open('a',encoding='utf-8') as f: f.write(separator+key+'\\n')\n"
            "os.chmod(target,0o600)\n"
        )
        result = await client.run(["/usr/bin/python3", "-c", script, encoded])
        if not result.ok:
            raise BootstrapError("authorized_keys", "The Pi Manager key could not be appended")

    async def _install_helper(
        self,
        client: SSHClientProtocol,
        password: str,
        temporary_paths: list[str],
        filename: str,
        destination: str,
        mode: int,
    ) -> None:
        source = self.helper_dir / filename
        try:
            content = await asyncio.to_thread(source.read_text, encoding="utf-8")
        except OSError as err:
            raise BootstrapError("helper_source", "The bundled helper source is unavailable") from err
        temporary = f"/tmp/.pi-manager-{secrets.token_hex(12)}"
        temporary_paths.append(temporary)
        await client.upload_text(temporary, content, mode=0o600)
        result = await client.run_sudo(
            [
                "/usr/bin/install",
                "-D",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                oct(mode)[2:],
                temporary,
                destination,
            ],
            password=password,
        )
        if not result.ok:
            raise BootstrapError("helper_install", "The remote helper could not be installed")

    async def _install_sudoers(
        self,
        client: SSHClientProtocol,
        password: str,
        temporary_paths: list[str],
        content: str,
    ) -> None:
        temporary = f"/tmp/.pi-manager-sudoers-{secrets.token_hex(12)}"
        validation_path = f"/etc/sudoers.d/.pi-manager.validate-{secrets.token_hex(8)}"
        temporary_paths.append(temporary)
        await client.upload_text(temporary, content, mode=0o600)
        activated = False
        try:
            install = await client.run_sudo(
                [
                    "/usr/bin/install",
                    "-D",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "0440",
                    temporary,
                    validation_path,
                ],
                password=password,
            )
            if not install.ok:
                raise BootstrapError("sudoers_install", "The restricted sudo policy could not be staged")
            check = await client.run_sudo(["/usr/sbin/visudo", "-cf", validation_path], password=password)
            if not check.ok:
                raise BootstrapError("sudoers_validation", "The restricted sudo policy failed visudo validation")
            activate = await client.run_sudo(["/bin/mv", validation_path, REMOTE_SUDOERS_PATH], password=password)
            if not activate.ok:
                raise BootstrapError("sudoers_activate", "The restricted sudo policy could not be activated")
            activated = True
        finally:
            if not activated:
                try:
                    await client.run_sudo(["/bin/rm", "-f", validation_path], password=password)
                except Exception:  # noqa: BLE001 - cleanup must not mask the staged failure
                    pass

    async def _configure_remote(
        self,
        client: SSHClientProtocol,
        key: Any,
        services: tuple[str, ...],
        options: Mapping[str, Any],
    ) -> None:
        trust = await client.run_sudo_nopass(
            [REMOTE_CTL_PATH, "configure-trust", "--json", "--secret", key.upgrade_secret]
        )
        if not trust.ok:
            raise BootstrapError("remote_trust", "The remote helper upgrade trust record could not be installed")
        configuration = {
            "services": list(services),
            CONF_FILESYSTEM_INCLUDE: list(options.get(CONF_FILESYSTEM_INCLUDE, [])),
            CONF_FILESYSTEM_EXCLUDE: list(options.get(CONF_FILESYSTEM_EXCLUDE, [])),
            CONF_NETWORK_INCLUDE: list(options.get(CONF_NETWORK_INCLUDE, [])),
            CONF_NETWORK_EXCLUDE: list(options.get(CONF_NETWORK_EXCLUDE, [])),
        }
        encoded = base64.b64encode(json.dumps(configuration).encode("utf-8")).decode("ascii")
        result = await client.run_sudo_nopass(
            [REMOTE_CTL_PATH, "configure-services", "--json", "--services-json", encoded]
        )
        if not result.ok:
            raise BootstrapError("remote_configuration", "The remote helper configuration could not be applied")

    def _render_sudoers(self, username: str) -> str:
        try:
            template = (self.helper_dir / "sudoers.template").read_text(encoding="utf-8")
        except OSError as err:
            raise BootstrapError("sudoers_source", "The bundled sudoers policy is unavailable") from err
        return template.replace("{username}", username)


def _helper_version(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("AGENT_VERSION"):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"
