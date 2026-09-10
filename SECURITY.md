# Pi Manager security model

Pi Manager is deliberately narrow: Home Assistant communicates with a managed
host over SSH and invokes a versioned, root-owned helper through a restricted
sudoers policy. The managed host does not run a Pi Manager listener, MQTT
service, HTTP agent, WebSocket daemon, or generic terminal.

## Bootstrap and secrets

- The setup password exists only in the config-flow task and transient sudo
  input. It is not written to the config entry, entity attributes, diagnostics,
  helper files, or logs after successful key verification.
- Each remote machine ID receives an independent Ed25519 keypair and a
  per-host 32-byte helper-upgrade trust secret. Private material is kept under
  the integration-owned `pi_manager_keys/` directory with restrictive modes;
  the Home Assistant-managed `.storage` area is not written directly.
- The public key is appended idempotently to the user's
  `~/.ssh/authorized_keys`. The file is never truncated or replaced, and a
  symlink is rejected.

## Host trust

The SSH transport obtains the server key fingerprint through AsyncSSH's host
key callback. First trust is shown in the confirmation step. Subsequent
connections compare the observed fingerprint with the config entry and fail
closed on mismatch. There is no `StrictHostKeyChecking=no` equivalent and no
silent trust-record replacement.

## Remote privilege boundary

Bootstrap installs:

```text
/usr/local/lib/pi-manager/pi_manager_agent.py
/usr/local/sbin/pi-managerctl
/etc/sudoers.d/pi-manager
```

The files are installed as root-owned artifacts with explicit modes, and the
sudoers file is checked with `visudo -cf` before activation. Sudo grants only
the Pi Manager entry point and its explicit operation shapes. The helper then
validates every operation, service name, allowlist membership, encoded
configuration size, update-job identifier, and signed helper upgrade before
performing a privileged action. The policy never grants `/bin/sh`, `/bin/bash`,
arbitrary `systemctl`, arbitrary `apt`, or `NOPASSWD: ALL`.

Helper state and configuration are written atomically. All package-changing
jobs are selected from a fixed command table, serialized per host, and run
through a root-owned systemd-managed background worker; they do not keep a
Home Assistant request open and never reboot automatically. The worker is
root-only and is not included in the normal sudoers allowlist. Preview, audit,
hold, and failed-service queries expose bounded summaries rather than raw apt
or systemd output.

## Diagnostics and frontend

Diagnostics redact passwords, key references, fingerprints, private keys,
`authorized_keys`, environment data, and unbounded command output. The
dashboard uses public Home Assistant registry APIs and the public custom card
and dashboard-strategy registration mechanism. It does not write Lovelace
storage or create an unauthenticated control endpoint. Reboot, shutdown, all
package-changing controls, and allowlisted service restarts are optional and
require an in-UI confirmation. Every control is rendered and executed for one
host card only; no fleet action exists.

## Security test coverage

The automated suite covers password absence during bootstrap, per-host key
uniqueness and reuse, fingerprint mismatch handling, append-only key
installation behavior, sudo-policy staging, service-name injection rejection,
signed helper upgrades, update-job serialization, dangerous-control gating,
diagnostics redaction, and multi-host action isolation. A real Debian/Raspberry
Pi validation remains a manual release gate documented in
`ACCEPTANCE_TESTS.md`.
