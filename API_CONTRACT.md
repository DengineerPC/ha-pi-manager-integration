# Pi Manager remote helper contract

The integration ships the helper source in the release. It never downloads
executable helper code from an arbitrary URL at runtime. The helper is invoked
over the already trusted SSH connection through:

```text
/usr/local/sbin/pi-managerctl
```

Every operation writes one bounded JSON object to stdout. Operational detail
goes to stderr. Every JSON object includes `schema_version: 1`; clients reject
unknown schema versions rather than guessing at field meanings.

## Explicit operations

The only supported operations are:

```text
status --json
check-updates --json
update --json
upgrade --json
dist-upgrade --json
preview-upgrade --json
preview-dist-upgrade --json
preview-autoremove --json
audit-packages --json
show-package-holds --json
failed-services --json
configure-packages --json
repair-packages --json
autoremove --json
autoclean --json
clean-cache --json
reboot --json
shutdown --json
service-status NAME --json
service-restart NAME --json
```

Bootstrap and signed helper maintenance also use the explicitly implemented
operations `service-validate`, `configure-services`, `configure-trust`, and
`upgrade-helper`. The root-only internal `job-worker JOB_TYPE` operation is
used only as the executable of a Pi Manager-owned transient systemd unit; it
is not granted in sudoers and cannot be used as a normal remote management
operation. There is no generic command, shell, apt-argument, systemd unit,
package-name, or terminal mode.

The restricted `/etc/sudoers.d/pi-manager` policy authorizes only these exact
entry-point shapes (the helper still performs its own validation):

```text
/usr/local/sbin/pi-managerctl status --json
/usr/local/sbin/pi-managerctl check-updates --json
/usr/local/sbin/pi-managerctl update --json
/usr/local/sbin/pi-managerctl upgrade --json
/usr/local/sbin/pi-managerctl dist-upgrade --json
/usr/local/sbin/pi-managerctl preview-upgrade --json
/usr/local/sbin/pi-managerctl preview-dist-upgrade --json
/usr/local/sbin/pi-managerctl preview-autoremove --json
/usr/local/sbin/pi-managerctl audit-packages --json
/usr/local/sbin/pi-managerctl show-package-holds --json
/usr/local/sbin/pi-managerctl failed-services --json
/usr/local/sbin/pi-managerctl configure-packages --json
/usr/local/sbin/pi-managerctl repair-packages --json
/usr/local/sbin/pi-managerctl autoremove --json
/usr/local/sbin/pi-managerctl autoclean --json
/usr/local/sbin/pi-managerctl clean-cache --json
/usr/local/sbin/pi-managerctl reboot --json
/usr/local/sbin/pi-managerctl shutdown --json
/usr/local/sbin/pi-managerctl service-status NAME
/usr/local/sbin/pi-managerctl service-restart NAME
/usr/local/sbin/pi-managerctl service-validate NAME
/usr/local/sbin/pi-managerctl configure-services --json --services-json VALUE
/usr/local/sbin/pi-managerctl configure-trust --json --secret VALUE
/usr/local/sbin/pi-managerctl upgrade-helper --json --version VALUE --agent VALUE --ctl VALUE --signature VALUE
/usr/local/sbin/pi-managerctl upgrade-helper --json --version VALUE --agent VALUE --ctl VALUE --signature VALUE --sudoers VALUE
```

`NAME` and `VALUE` represent the single bounded argument accepted by the
corresponding helper operation; they are not shell fragments. `job-worker` is
deliberately absent from this list.

## Package operation mapping

The following fixed operations are the complete package-management allowlist:

| Pi Manager operation | Fixed host command | Runs in background |
| --- | --- | --- |
| `update` / `upgrade` | `apt-get -y upgrade` | Yes |
| `dist-upgrade` | `apt-get -y dist-upgrade` | Yes |
| `configure-packages` | `dpkg --configure -a` | Yes |
| `repair-packages` | `apt-get -f -y install` | Yes |
| `autoremove` | `apt-get -y autoremove` (never purge) | Yes |
| `autoclean` | `apt-get autoclean` | Yes |
| `clean-cache` | `apt-get clean` | Yes |
| `preview-upgrade` | `apt-get -s -q upgrade` | No |
| `preview-dist-upgrade` | `apt-get -s -q dist-upgrade` | No |
| `preview-autoremove` | `apt-get -s -q autoremove` | No |

The normal upgrade, dist-upgrade, repair, and autoremove workers refresh apt
metadata first with the fixed `apt-get update -q` command. No package names or
additional arguments can be supplied by Home Assistant. The modern apt
`full-upgrade` concept is represented by the explicit `dist-upgrade` action.
Package jobs are serialized per host, return after their systemd job starts,
and never reboot automatically.

## Status response

`status --json` returns:

```json
{
  "schema_version": 1,
  "agent_version": "0.2.5",
  "policy_version": "0.2.5",
  "machine": {
    "machine_id": "...",
    "hostname": "pi-nas",
    "model": "Raspberry Pi 5 Model B",
    "os": "Debian GNU/Linux 13",
    "kernel": "6.12.0-rpi",
    "arch": "aarch64",
    "uptime_seconds": 123456
  },
  "cpu": {
    "usage_percent": 12.4,
    "temperature_c": 48.2,
    "load_1": 0.18,
    "load_5": 0.21,
    "load_15": 0.16
  },
  "memory": {
    "total_bytes": 8589934592,
    "used_bytes": 3123456789,
    "used_percent": 36.4
  },
  "filesystems": [],
  "network": [],
  "updates": {
    "last_checked": "2026-09-07T10:00:00Z",
    "available": 7,
    "security": 2,
    "reboot_required": false
  },
  "services": [],
  "job": null,
  "maintenance": {
    "previews": [],
    "audit": null,
    "holds": null,
    "failed_services": null
  }
}
```

Filesystem entries contain `mount`, `device`, `fstype`, `total_bytes`,
`used_bytes`, `used_percent`, and `readonly`. Pseudo and temporary
filesystems are excluded by default, while mounted persistent volumes are
discovered without a hard-coded mount path. Service entries contain `name`,
`active`, `state`, and `substate`. When the fixed `tailscaled.service` unit is
installed, it is included automatically as a read-only service entry; when it
is absent, no Tailscale service entry is emitted. Network entries continue to
include a present `tailscale0` interface subject to the normal network filters.
Pi Manager never invokes the Tailscale CLI or exposes Tailscale credentials.
A job contains `id`, `type`, `state`, `started_at`, `finished_at`, `exit_code`,
and a bounded `summary`. `type` is one of the fixed background package
operation names.

The optional `maintenance` object retains the latest bounded results for
manual inspection buttons. `previews` contains at most one result for each
preview operation. Each preview contains `operation`, `last_checked`,
`upgraded`, `newly_installed`, `to_remove`, `not_upgraded`,
`reboot_required`, and `summary`. `audit` contains `last_checked`, `healthy`,
`dpkg_issues`, `apt_healthy`, and `summary`. `holds` contains
`last_checked` and a bounded `packages` list. `failed_services` contains
`last_checked` and a bounded `services` list. Older helpers may omit
`maintenance`; clients treat the omission as empty optional state. Older
helpers may also omit `policy_version`; clients treat that as `"unknown"`,
which causes the signed policy migration to be retried.

`update --json` starts a serialized systemd-managed normal upgrade and returns
a job identifier promptly. The other background operations use the same job
contract. A second package job while the first is running returns the stable
error code `update_already_running` for compatibility. The helper never
reboots as a side-effect of a package operation.

## Errors and compatibility

Rejected operations return a non-zero exit code and an object shaped like:

```json
{
  "schema_version": 1,
  "error": {
    "code": "service_not_allowlisted",
    "message": "service_not_allowlisted"
  }
}
```

Responses and command output are bounded. A breaking contract change requires
incrementing `SCHEMA_VERSION`, preserving the old parser during migration, and
updating this document and its fixtures in the same release.

## Signed helper and policy migration

The integration keeps the JSON schema at `1` while releasing helper `0.2.5`
and integration `0.2.6`. An existing host is repaired without a bootstrap
password, key rotation, machine-ID change, or host-fingerprint reset.

For the first compatibility stage, the HMAC message is:

```text
version + NUL + agent_source + NUL + ctl_source
```

After that legacy-signed helper is installed, the new helper accepts the
extended form for the policy migration:

```text
version + NUL + agent_source + NUL + ctl_source + NUL + sudoers_source
```

The candidate policy must contain the matching `# Pi Manager sudo policy
version: 0.2.5` marker, a root-only `NOPASSWD` target, and exactly the command
matrix above. The helper writes it beside the active file with mode `0440`,
runs `/usr/sbin/visudo -cf` against the staged file, and atomically replaces
only `/etc/sudoers.d/pi-manager` after validation. `status --json` reports the
validated marker as `policy_version`; malformed or incomplete policies report
`"unknown"` and are not considered repaired.

If a non-JSON helper failure contains the exact `sudo: a password is required`
condition, the Home Assistant runtime reports the bounded category
`helper_privilege_denied`; it does not expose raw stderr. Other helper errors
retain their JSON error code or use `helper_command_failed`.
