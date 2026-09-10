"""Typed errors exposed by Pi Manager transport and bootstrap boundaries."""

from __future__ import annotations


class PiManagerError(Exception):
    """Base error which is safe to translate at the integration boundary."""


class PiManagerConnectionError(PiManagerError):
    """The host could not be reached within the configured timeout."""


class PiManagerAuthenticationError(PiManagerError):
    """SSH authentication failed."""


class HostFingerprintMismatch(PiManagerError):
    """The observed SSH host key differs from the stored trust record."""

    def __init__(self, expected: str | None, observed: str | None) -> None:
        super().__init__("The SSH host fingerprint does not match the trusted fingerprint")
        self.expected = expected
        self.observed = observed


class HostFingerprintUnavailable(PiManagerError):
    """No host fingerprint could be obtained from the SSH handshake."""


class HostDiscoveryError(PiManagerError):
    """The host did not satisfy a required discovery prerequisite."""

    def __init__(self, category: str, detail: str = "") -> None:
        super().__init__(category)
        self.category = category
        self.detail = detail


class HelperProtocolError(PiManagerError):
    """The remote helper returned invalid or unsupported JSON."""


class BootstrapError(PiManagerError):
    """Bootstrap failed at a named, retryable stage."""

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(stage)
        self.stage = stage
        self.detail = detail


class ServiceNameError(PiManagerError, ValueError):
    """A service name was invalid or not allowlisted."""


class UpdateAlreadyRunning(PiManagerError):
    """The host already has a package update job in progress."""


class HelperIncompatibleError(PiManagerError):
    """The installed helper cannot satisfy the integration contract."""
