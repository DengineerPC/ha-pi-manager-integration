# Pi Manager acceptance matrix

The labels are intentional:

- `PASS` means the check is automated and passed locally in this workspace.
- `NOT RUN` means it requires a real Home Assistant installation, HACS, or a
  reachable supported Debian/Raspberry Pi host and has not been claimed from
  source-only tests.
- `BLOCKED` means an external prerequisite is unavailable.

## Automated checks

| Area | Check | Evidence | Status |
| --- | --- | --- | --- |
| Contract | Healthy and update fixtures parse with schema version 1; malformed and oversized responses fail | `tests/test_contract*.py`, `tests/test_remote_agent.py` | PASS |
| Validation | Host, username, port, service syntax and allowlist reject injection | `tests/test_validation.py`, `tests/test_actions.py` | PASS |
| SSH | Output is bounded and transport is argument-list based | `tests/test_ssh.py` | PASS |
| Discovery | Supported prerequisites, Raspberry Pi 1 `armv6l`, unknown-architecture rejection, missing apt, target writability and secret-safe calls | `tests/test_discovery.py` | PASS |
| Flow | Setup fields, actionable error mapping and password-free entry shape | `tests/test_config_flow.py` | PASS |
| Coordinator | One status call, offline failure and recovery; package checks obey cadence | `tests/test_coordinator.py` | PASS |
| Entities | Machine-ID stable IDs, filesystem discovery, connectivity and optional Tailscale service state | `tests/test_entities.py` | PASS |
| Bootstrap | Password-free final path, read-only failure and retry-safe staging | `tests/test_bootstrap.py` | PASS |
| Key storage | Independent Ed25519/trust material and idempotent reuse | `tests/test_key_store.py` | PASS |
| Helper | Filesystem/network filters, schema output, fixed optional Tailscale probe, service validation, update serialization | `tests/test_remote_agent.py` | PASS |
| Maintenance | Fixed per-host package previews, audit/holds/failed-service queries, serialized background package jobs, root-only worker, and rejection of extra package arguments | `tests/test_remote_agent.py`, `tests/test_contract.py`, `tests/test_actions.py` | PASS |
| Policy migration | Complete button command matrix, signed extended policy payload, exact remote validation, `visudo`-before-activation and policy-version reporting | `tests/test_sudoers.py`, `tests/test_remote_agent.py`, `tests/test_upgrade.py` | PASS |
| Upgrade | Signed, per-host two-stage helper/policy upgrade is attempted once and preserves identity, trust and key | `tests/test_upgrade.py` | PASS |
| Remote helper compatibility | Shipped agent and suffixless control wrapper have Python 3.13-compatible source; helper marker matches the shared version | `tests/test_remote_helper_compatibility.py`, integration CI Python 3.13 job | PASS |
| Transport recovery | AsyncSSH channel failures drop the affected cached client and sudo privilege failures use a bounded stable code | `tests/test_ssh.py`, `tests/test_runtime.py` | PASS |
| Diagnostics | Password, key material and fingerprint are redacted | `tests/test_diagnostics.py` | PASS |
| Multi-host | Keys and refresh actions remain entry-local | `tests/test_multi_host.py` | PASS |

Run the backend gate from this repository with:

```powershell
python -m ruff format --check custom_components tests
python -m ruff check custom_components tests
python -m mypy custom_components
python -m pytest tests -q
```

## Manual release gates

These checks require a disposable Home Assistant 2026.5+ instance and a
supported Raspberry Pi OS/Debian host. Record the date, Home Assistant
version, host OS/architecture, and result before changing `NOT RUN` to
`PASS`.

| ID | Exact verification | Status |
| --- | --- | --- |
| M-01 | Add both repositories as the correct HACS Integration and Dashboard custom repositories; install each without copying files; restart Home Assistant; confirm the integration appears under Settings → Devices & services and the dashboard resource is served from HACS. | NOT RUN |
| M-02 | Add a disposable Debian/Raspberry Pi host with host, port, username, and bootstrap password; confirm the fingerprint/capability preview; continue; inspect the final config entry and confirm no password field exists. | NOT RUN |
| M-03 | On the host, verify the helper paths, root ownership, modes, and `sudo -l -U <username>`; run `sudo visudo -cf /etc/sudoers.d/pi-manager`; confirm the complete explicit status, check, preview, audit, holds, failed-service, package-job, service, power, bootstrap, and both helper-upgrade forms are present, while `job-worker`, shells, arbitrary apt/systemctl, and `NOPASSWD: ALL` are absent. | NOT RUN |
| M-04 | Re-run setup against the same machine; confirm the existing key and helper are reused without duplicate `authorized_keys` lines or a second Home Assistant device. | NOT RUN |
| M-05 | Change or replace the host SSH key on a disposable host; trigger a refresh; confirm entities become unavailable, the fingerprint repair issue appears, and no new key is silently trusted. | NOT RUN |
| M-06 | Stop SSH/network access; confirm Home Assistant remains responsive and the Pi Manager device remains present but unavailable; restore access and confirm automatic recovery. | NOT RUN |
| M-07 | Mount a persistent external volume such as `/mnt/extstorage`; refresh; confirm it appears; confirm pseudo/temporary filesystems do not appear by default. | NOT RUN |
| M-08 | Use Check updates and Install updates; confirm the install action returns a job ID promptly, a second install is rejected, status later reports completion/failure, and no automatic reboot occurs. | NOT RUN |
| M-09 | Configure `smbd.service`; confirm status and restart target only that allowlisted unit; attempt `smbd;reboot`, `../../something.service`, `$(reboot).service`, and an unallowlisted unit; confirm all are rejected. | NOT RUN |
| M-10 | Enable dangerous controls; from the card and generated dashboard attempt reboot, shutdown, and install; confirm each requires confirmation. Disable the option and confirm the controls are absent/rejected. | NOT RUN |
| M-15 | On one disposable host, run each preview/audit/holds/failed-service action; confirm retained sensors update. Enable dangerous controls for that host only, start normal upgrade, `upgrade`, dist-upgrade, configure, repair, autoremove, autoclean, and clean actions one at a time; confirm each returns promptly with a job, a concurrent second job is rejected, and no automatic reboot occurs. | NOT RUN |
| M-16 | Confirm the Danger zone in the shared dashboard shows only the selected host's controls, wraps on mobile, has no fleet/all-host action, and does not duplicate the fleet online/status indicators. | NOT RUN |
| M-11 | Add two disposable hosts concurrently with different addresses and credentials; confirm independent machine-ID devices, keys, polling, service lists and actions. | NOT RUN |
| M-12 | Install the dashboard resource, open the Community dashboards picker, select Pi Manager, and confirm Overview plus one view per host. Add/remove a host and regenerate; confirm views change without editing Lovelace storage/YAML. | NOT RUN |
| M-13 | Download diagnostics for a configured host; inspect the export and confirm it contains no password, private key, raw `authorized_keys`, environment, or unbounded apt output. | NOT RUN |
| M-14 | Complete authenticated setup on a physical Raspberry Pi 1 reporting `armv6l`; confirm the host is accepted and receives an independent device. On a managed host with Tailscale installed, confirm `tailscaled.service` state and `tailscale0` traffic entities appear without any Tailscale credentials or control actions. | NOT RUN |
| M-17 | On each supported Python 3.13 host, trigger the signed helper/policy upgrade; confirm helper `0.2.5`, policy `0.2.5`, schema `1`, unchanged machine ID/fingerprint/key, valid status JSON, complete sudoers matrix, and cleared helper-contract repair. Do not use an unsigned/manual replacement. | NOT RUN |

The current source/package state is therefore suitable for focused automated
review, but it is not a claim of authenticated hardware, HACS, deployment, or
production acceptance until the applicable manual checks are executed.
