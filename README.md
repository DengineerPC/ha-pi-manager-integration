# Pi Manager Integration

Pi Manager is a Home Assistant custom integration for monitoring and safely
managing Raspberry Pi OS and compatible Debian hosts over SSH.

This is the public HACS Integration repository:

<https://github.com/DengineerPC/ha-pi-manager-integration>

Pi Manager targets Home Assistant `2026.5.0` or newer. It has no YAML setup
requirement and does not require the optional dashboard package.

[Open Pi Manager Integration in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=DengineerPC&repository=ha-pi-manager-integration&category=integration)

## Install through HACS

Until the repository is included in HACS's default catalog, add it as a custom
repository:

1. Open **HACS → Integrations**.
2. Open the HACS menu and choose **Custom repositories**.
3. Add `DengineerPC/ha-pi-manager-integration`.
4. Select **Integration** as the category.
5. Install **Pi Manager Integration** and restart Home Assistant.
6. Open **Settings → Devices & services → Add integration** and select
   **Pi Manager**.

The setup flow asks for a host/IP, SSH port, username, bootstrap password, and
optional display name. It validates the host, displays the SSH fingerprint and
capabilities, then creates a unique per-host Ed25519 key and installs the
restricted Pi Manager helper. After key-based verification succeeds, the
bootstrap password is removed from the Home Assistant config entry.

## Supported hosts

The initial target is Raspberry Pi OS Bookworm/Trixie and Debian 12 or newer
with OpenSSH, systemd, Python 3, sudo, and apt/dpkg. Raspberry Pi 1 hosts
reporting `armv6l` are supported when they meet those same prerequisites.
Compatible Debian hosts are supported when they expose the same capabilities.

The SSH account must be able to use password-authenticated sudo during the
one-time bootstrap. After bootstrap, the generated key is used and the
restricted Pi Manager sudo policy is the only privilege path used by normal
runtime operations.

## Security model

Pi Manager uses SSH only. It does not install a listening agent, MQTT service,
HTTP endpoint, WebSocket daemon, or terminal. The host receives root-owned,
versioned helper files and a sudoers policy that permits only the explicit Pi
Manager entry point. Service names are syntax-checked and allowlisted; arbitrary
shell, apt, systemd, and sudo commands are not exposed.

The first fingerprint is shown during setup. A later fingerprint change fails
closed and requires deliberate recovery. Removing the Home Assistant config
entry does not delete unrelated SSH configuration on the host.

Bootstrap-owned artifacts remain on the host if the Home Assistant entry is
removed: the generated public-key line, root-owned helper/state files, and the
Pi Manager sudoers file. This prevents removal from deleting unrelated
`authorized_keys` entries. No automatic remote cleanup is performed.

## Monitoring and controls

The options flow supports a 15-second minimum polling interval (30 seconds by
default), filesystem and network filters, an allowlisted monitored systemd
service list, optional apt checks with a six-hour default cadence, a CPU
temperature warning threshold, and an explicit dangerous-control switch.
Normal polling uses one bounded `status --json` call. Package checks run only
when enabled and due; updates run as serialized background systemd jobs.

The generated device uses `/etc/machine-id` for its stable identity. IP address
and hostname are connection details, not device identity. Core entities include
online/reboot/temperature-warning/service state, CPU/temperature/load/memory/
uptime/filesystem/network/update/helper-version sensors, package-job and
package-health sensors, and explicit per-host buttons for refresh, update
checks, package previews/audits, package maintenance, reboot, shutdown, and
allowlisted service restart.

Package changes run only on the selected host, return a job ID promptly, never
reboot automatically, and cannot be triggered as an all-host action. When
`tailscaled.service` is installed, Pi Manager exposes its read-only service
state and reports `tailscale0` traffic counters when that interface is present.
It does not install, authenticate, restart, or otherwise control Tailscale, and
it does not collect Tailscale credentials or tailnet status.

The dangerous-control option is per config entry. Leave it disabled to keep
package-changing, reboot, shutdown, and service-restart buttons unavailable.
Enable it separately for a host only after reviewing its preview results.

## Optional dashboard

Install the companion public repository for the responsive custom card and
dynamic dashboard strategy:

<https://github.com/DengineerPC/ha-pi-manager-dashboard>

The backend remains fully usable with standard Home Assistant entity cards when
the dashboard package is not installed.

## Docker Home Assistant

For Home Assistant Container, install the integration under the host directory
bound to `/config/custom_components/pi_manager/`, then restart the container.
Do not copy the files only into the container's ephemeral filesystem. HACS
handles this path automatically.

## Troubleshooting

Setup errors distinguish unavailable SSH, DNS failure, authentication failure,
host-key mismatch, missing Python/systemd/apt, insufficient sudo, read-only
targets, and incomplete bootstrap. Check the config-entry repair issue before
accepting a changed host key. Helper upgrades preserve the host fingerprint,
generated key, machine-ID device identity, and config-entry identity.

If an upgrade reports an incompatible helper, keep the host reachable and
resolve the repair issue through a deliberate reconfigure/upgrade retry. Do
not replace the SSH key or delete the config entry as a routine upgrade step.

The automated and manual acceptance matrix is in
[`ACCEPTANCE_TESTS.md`](ACCEPTANCE_TESTS.md). Source tests do not claim that
HACS, an authenticated Home Assistant instance, or a physical Raspberry Pi
has been verified.
