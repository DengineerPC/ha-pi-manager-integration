# Pi Manager Installation

This guide covers the public `0.2.2` HACS Integration release for Home
Assistant `2026.5.0` or newer.

## Prerequisites

- Home Assistant with HACS installed.
- A supported Raspberry Pi OS Bookworm/Trixie or Debian 12+ host.
- SSH enabled on the host.
- A host account with password SSH access for the one-time bootstrap.
- Temporary password-authenticated sudo for that account during bootstrap.
- Python 3, systemd, sudo, apt/dpkg, and a writable target filesystem.
- A current Home Assistant backup before installing a custom integration.

Pi Manager communicates over outbound SSH from Home Assistant. It does not
install an HTTP, MQTT, WebSocket, or terminal service on the managed host.

## HACS installation

1. Open **HACS → Integrations**.
2. Open the HACS menu and choose **Custom repositories**.
3. Add `DengineerPC/ha-pi-manager-integration`.
4. Choose **Integration** as the repository category.
5. Install **Pi Manager Integration**.
6. Restart Home Assistant.
7. Open **Settings → Devices & services → Add integration**.
8. Choose **Pi Manager**.

The setup flow requests the host/IP, SSH port (default `22`), username,
bootstrap password, and optional display name. Review the displayed SSH
fingerprint and capability summary before continuing.

After confirmation, the integration:

1. Generates a unique Ed25519 key for this host.
2. Appends one owned public-key line to the user's `authorized_keys` file.
3. Deploys the versioned root-owned helper and restricted sudoers policy.
4. Validates the policy with `visudo -cf`.
5. Verifies key-based `pi-managerctl status --json` access.
6. Removes the bootstrap password from the Home Assistant config entry.

The fingerprint is enforced on later connections. A changed fingerprint fails
closed; do not accept it without independently verifying the host identity.

## Home Assistant Container on Docker/OMV

HACS writes to the Home Assistant configuration directory used by the
container. With a Compose mapping such as:

```yaml
volumes:
  - ./config:/config
```

the integration is installed under the host-side `config/custom_components/`
directory. Do not copy the files only into the container's ephemeral layer.
Restart the `homeassistant` container after HACS prompts you to do so.

## Add another Pi

Repeat **Settings → Devices & services → Add integration → Pi Manager** for
each host. Every host receives an independent config entry, machine-ID device,
SSH fingerprint, key, coordinator, options, and action set. Never copy one
host's private key or configuration directory to another host.

## Enable management controls deliberately

Monitoring and read-only package inspection are available with dangerous
controls disabled. To expose package-changing actions, service restart,
reboot, and shutdown, open that host's **Configure** options and enable its
dangerous-control switch. The setting is per host; there are no all-host
actions. The dashboard asks for confirmation before destructive actions.

Package-changing actions start a serialized background job on the selected
host and return promptly. Closing a browser or SSH window does not cancel the
job. Pi Manager never reboots automatically after package work.

## Removal

Removing the Home Assistant config entry does not delete remote SSH
configuration or Pi Manager files. This is intentional. It prevents unrelated
`authorized_keys` entries from being destroyed. If you later perform manual
cleanup, remove only Pi Manager-owned artifacts after confirming the exact
paths; never delete the entire `authorized_keys` file.

## Troubleshooting

- **Pi Manager is not listed:** confirm HACS installed the integration and
  restart Home Assistant; the dashboard package is not required.
- **Authentication failed:** verify the username, port, password, and host
  password-authentication policy. The password is bootstrap-only.
- **Fingerprint changed:** verify the host out of band, then use the deliberate
  repair/reconfigure path. Do not silently replace the trusted key.
- **Missing Python/systemd/apt/sudo:** install the supported host prerequisites
  and retry. Pi Manager does not fall back to broad sudo or a shell.
- **Architecture unsupported:** Raspberry Pi 1 normally reports `armv6l` and
  is accepted when the supported OS prerequisites are present.

The complete automated and manual acceptance matrix is in
[`ACCEPTANCE_TESTS.md`](ACCEPTANCE_TESTS.md).
