"""Async SSH transport with explicit host-key verification.

The transport deliberately exposes an argument-list interface to the rest of
the integration. AsyncSSH sends a remote command through the SSH exec channel;
`shlex.join` is used only to encode already validated arguments for that
protocol boundary. No caller can submit a free-form command string.
"""

from __future__ import annotations

import asyncio
import importlib
import shlex
from collections.abc import Sequence
from typing import Any, Protocol

from .const import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_OUTPUT
from .errors import (
    HostFingerprintMismatch,
    HostFingerprintUnavailable,
    PiManagerAuthenticationError,
    PiManagerConnectionError,
)
from .models import CommandResult


class SSHClientProtocol(Protocol):
    """Operations needed by discovery, bootstrap, and runtime actions."""

    host: str
    port: int
    username: str
    fingerprint: str | None

    async def connect(
        self,
        *,
        password: str | None = None,
        private_key: str | None = None,
        allow_first_trust: bool = False,
        expected_fingerprint: str | None = None,
    ) -> None: ...

    async def run(self, args: Sequence[str], *, timeout: float = DEFAULT_COMMAND_TIMEOUT) -> CommandResult: ...

    async def run_sudo(
        self,
        args: Sequence[str],
        *,
        password: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ) -> CommandResult: ...

    async def run_sudo_nopass(
        self, args: Sequence[str], *, timeout: float = DEFAULT_COMMAND_TIMEOUT
    ) -> CommandResult: ...

    async def upload_text(self, path: str, content: str, *, mode: int = 0o600) -> None: ...

    async def close(self) -> None: ...


def _bounded(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_COMMAND_OUTPUT:
        return value
    return encoded[:MAX_COMMAND_OUTPUT].decode("utf-8", errors="ignore")


class AsyncSSHClient:
    """AsyncSSH-backed client with an explicit host-key callback."""

    def __init__(self, host: str, port: int, username: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.fingerprint: str | None = None
        self._connection: Any = None
        self._asyncssh_module: Any = None

    async def connect(
        self,
        *,
        password: str | None = None,
        private_key: str | None = None,
        allow_first_trust: bool = False,
        expected_fingerprint: str | None = None,
    ) -> None:
        """Connect and require the callback to approve the observed host key."""

        asyncssh = await _async_import_asyncssh()
        self._asyncssh_module = asyncssh

        owner = self

        class HostKeyClient(asyncssh.SSHClient):  # type: ignore[misc, name-defined]
            def validate_host_public_key(self, host: str, addr: str, port: int, key: Any) -> bool:
                del host, addr, port
                observed = key.get_fingerprint("sha256")
                owner.fingerprint = observed
                return _verify_fingerprint(observed, expected_fingerprint, allow_first_trust)

        connect_kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            # An empty known-hosts set delegates the decision to the callback
            # above. This is not a host-key bypass: unknown and changed keys
            # are explicitly accepted/rejected by the callback.
            "known_hosts": [],
            "client_factory": HostKeyClient,
            "connect_timeout": DEFAULT_COMMAND_TIMEOUT,
        }
        if password is not None:
            connect_kwargs["password"] = password
        if private_key is not None:
            connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]

        try:
            self._connection = await asyncssh.connect(**connect_kwargs)
        except HostFingerprintMismatch:
            raise
        except asyncssh.PermissionDenied as err:
            raise PiManagerAuthenticationError("SSH authentication failed") from err
        except (TimeoutError, asyncssh.Error, OSError) as err:
            raise PiManagerConnectionError("SSH connection failed") from err
        if not self.fingerprint:
            await self.close()
            raise HostFingerprintUnavailable("SSH did not provide a host fingerprint")

    async def run(self, args: Sequence[str], *, timeout: float = DEFAULT_COMMAND_TIMEOUT) -> CommandResult:
        return await self._run(args, timeout=timeout, input_data=None)

    async def run_sudo(
        self,
        args: Sequence[str],
        *,
        password: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ) -> CommandResult:
        if not password:
            raise PiManagerAuthenticationError("Bootstrap authentication is required")
        return await self._run(
            ["/usr/bin/sudo", "-S", "-p=", "--", *args],
            timeout=timeout,
            input_data=f"{password}\n",
        )

    async def run_sudo_nopass(self, args: Sequence[str], *, timeout: float = DEFAULT_COMMAND_TIMEOUT) -> CommandResult:
        return await self._run(["/usr/bin/sudo", "-n", "--", *args], timeout=timeout, input_data=None)

    async def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        input_data: str | None,
    ) -> CommandResult:
        if self._connection is None:
            raise PiManagerConnectionError("SSH connection is not open")
        if not args or any(not isinstance(item, str) or not item for item in args):
            raise ValueError("SSH command arguments must be non-empty strings")
        command = shlex.join(list(args))
        try:
            result = await self._connection.run(
                command,
                check=False,
                input=input_data,
                timeout=timeout,
            )
        except (TimeoutError, OSError) as err:
            raise PiManagerConnectionError("SSH command failed") from err
        except Exception as err:  # noqa: BLE001 - translate only known AsyncSSH transport failures
            asyncssh_error = getattr(self._asyncssh_module, "Error", None)
            if isinstance(asyncssh_error, type) and isinstance(err, asyncssh_error):
                raise PiManagerConnectionError("SSH command failed") from err
            raise
        return CommandResult(
            returncode=int(result.exit_status),
            stdout=_bounded(result.stdout or ""),
            stderr=_bounded(result.stderr or ""),
        )

    async def upload_text(self, path: str, content: str, *, mode: int = 0o600) -> None:
        """Upload a temporary user-owned file using SFTP."""

        if self._connection is None:
            raise PiManagerConnectionError("SSH connection is not open")
        try:
            async with self._connection.start_sftp_client() as sftp:
                async with sftp.open(path, "w") as remote_file:
                    await remote_file.write(content)
                await sftp.chmod(path, mode)
        except (TimeoutError, OSError) as err:
            raise PiManagerConnectionError("SSH file transfer failed") from err
        except Exception as err:  # noqa: BLE001 - translate only known AsyncSSH transport failures
            asyncssh_error = getattr(self._asyncssh_module, "Error", None)
            if isinstance(asyncssh_error, type) and isinstance(err, asyncssh_error):
                raise PiManagerConnectionError("SSH file transfer failed") from err
            raise

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            await self._connection.wait_closed()
            self._connection = None


async def async_create_client(host: str, port: int, username: str) -> AsyncSSHClient:
    """Create a client without opening a connection yet."""

    return AsyncSSHClient(host, port, username)


async def _async_import_asyncssh() -> Any:
    """Load AsyncSSH off Home Assistant's event loop on first use."""

    try:
        return await asyncio.to_thread(importlib.import_module, "asyncssh")
    except (ImportError, OSError) as err:  # pragma: no cover - HA installs manifest requirements
        raise PiManagerConnectionError("SSH transport dependency is unavailable") from err


def _verify_fingerprint(observed: str, expected: str | None, allow_first_trust: bool) -> bool:
    """Apply the first-trust or stored-fingerprint decision."""

    if expected is None:
        if not allow_first_trust:
            raise HostFingerprintMismatch(None, observed)
        return True
    if observed != expected:
        raise HostFingerprintMismatch(expected, observed)
    return True
