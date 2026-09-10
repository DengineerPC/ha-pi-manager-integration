"""Per-host private-key storage with restrictive local permissions."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
from pathlib import Path

from .errors import BootstrapError
from .models import KeyMaterial

_MACHINE_ID_RE = re.compile(r"^[a-fA-F0-9]{16,128}$")


class KeyStore:
    """Store one Ed25519 keypair per remote machine ID."""

    def __init__(self, config_dir: str | Path) -> None:
        # Keep private key material in an integration-owned directory rather
        # than writing Home Assistant's managed .storage area directly.
        self.root = Path(config_dir) / "pi_manager_keys"

    async def async_create_for_machine(self, machine_id: str) -> KeyMaterial:
        return await asyncio.to_thread(self._create_for_machine, machine_id)

    async def async_load(self, key_id: str) -> str:
        return await asyncio.to_thread(self._load, key_id)

    async def async_load_material(self, key_id: str) -> KeyMaterial:
        return await asyncio.to_thread(self._load_material, key_id)

    def _create_for_machine(self, machine_id: str) -> KeyMaterial:
        if not _MACHINE_ID_RE.fullmatch(machine_id):
            raise BootstrapError("key_generation", "Remote machine identity is invalid")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        key_id = machine_id.lower()
        private_path = self.root / f"{key_id}.key"
        public_path = self.root / f"{key_id}.pub"
        secret_path = self.root / f"{key_id}.hmac"
        if private_path.exists() and public_path.exists():
            if not secret_path.exists():
                _write_file(secret_path, base64.b64encode(secrets.token_bytes(32)).decode("ascii"), 0o600)
            return KeyMaterial(
                key_id,
                private_path.read_text(encoding="utf-8"),
                public_path.read_text(encoding="utf-8"),
                secret_path.read_text(encoding="utf-8").strip(),
            )
        if private_path.exists() != public_path.exists():
            raise BootstrapError("key_generation", "The local per-host keypair is incomplete")
        try:
            import asyncssh

            private_key = asyncssh.generate_private_key("ssh-ed25519")
            private_text = _as_text(private_key.export_private_key())
            public_text = _as_text(private_key.export_public_key()).strip() + "\n"
        except (ImportError, OSError, ValueError) as err:
            raise BootstrapError("key_generation", "Ed25519 key generation is unavailable") from err
        _write_private(private_path, private_text)
        _write_file(public_path, public_text, 0o644)
        upgrade_secret = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        _write_file(secret_path, upgrade_secret, 0o600)
        return KeyMaterial(key_id, private_text, public_text, upgrade_secret)

    def _load(self, key_id: str) -> str:
        if not _MACHINE_ID_RE.fullmatch(key_id):
            raise BootstrapError("key_load", "Key reference is invalid")
        path = self.root / f"{key_id.lower()}.key"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as err:
            raise BootstrapError("key_load", "The per-host SSH key is unavailable") from err

    def _load_material(self, key_id: str) -> KeyMaterial:
        if not _MACHINE_ID_RE.fullmatch(key_id):
            raise BootstrapError("key_load", "Key reference is invalid")
        prefix = self.root / key_id.lower()
        try:
            return KeyMaterial(
                key_id=key_id.lower(),
                private_key=(prefix.with_suffix(".key")).read_text(encoding="utf-8"),
                public_key=(prefix.with_suffix(".pub")).read_text(encoding="utf-8"),
                upgrade_secret=(prefix.with_suffix(".hmac")).read_text(encoding="utf-8").strip(),
            )
        except OSError as err:
            raise BootstrapError("key_load", "The per-host SSH key or upgrade trust secret is unavailable") from err


def _as_text(value: str | bytes) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _write_private(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        os.chmod(path, 0o600)


def _write_file(path: Path, content: str, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
