# Exordos Usage

The `usage` element collects allocated CPU, RAM and disk capacity for managed
realm VMs from VictoriaMetrics. It stores interval consumption and resource
ownership history in PostgreSQL and exposes project-scoped statistics.

Realm ownership comes from the existing ecosystem `/v1/realms/` API. The
collector's dedicated role has only `exordos_ecosystem.realm.read`.
Financial accounts, tariffs and balance operations remain in ecosystem.
The ecosystem service user has the read-only `usage_viewer` role. Its unscoped
requests must specify `project_id`. The internal API address is
`http://usage.local.genesis-core.tech:8080`.

## Project structure

```text
exordos_usage/
    cmd/                 Service entry points
    common/              Configuration, HTTP clients and OpenAPI generation
    services/            gcl_looper collection service
    user_api/api/        RestAlchemy application, routes and controllers
    user_api/dm/         Storage models, usage integration and report queries
    tests/unit/          Tests without external services
    tests/functional/    PostgreSQL and authenticated API tests
migrations/              RestAlchemy migration steps
etc/exordos_usage/        Service configuration template and logging YAML
etc/systemd/             User API and collector services
exordos/                 Element manifest and image scripts
```

## Development

Python 3.14 is the default development and CI version. Install the `libev`
development headers required by Bjoern before installing Python dependencies.

```bash
uv sync --extra test
uv run --extra test pytest exordos_usage/tests/unit
uv run --extra test ruff check .
uv run --extra test ruff format --check .
```

Run PostgreSQL tests against a disposable database. Each test uses a separate
schema and removes it afterward.

```bash
USAGE_TEST_DSN=postgresql://localhost/usage_test uv run --extra test pytest exordos_usage/tests/functional
```

## Configuration and startup

Options are declared with `oslo.config` in `exordos_usage/common/config.py`.
Render `etc/exordos_usage/exordos_usage.conf.j2` into
`/etc/exordos_usage/exordos_usage.conf` and place `logging.yaml` beside it.
Create `/etc/exordos_usage/exordos_usage.d` for additional `.conf` files.
Keep the configuration private: it contains service and database credentials.

Configure the database URL, VictoriaMetrics endpoint and reader credentials,
the ecosystem service credentials, and
the IAM client endpoint, audience and JWKS key. The VictoriaMetrics reader
must permit `/api/v1/export`. An empty `ecosystem.host_realm` selects the
central host realm. Metrics are selected by node UUIDs from the recorded
realm bindings.

Apply SDK migrations first, then usage migrations, before starting services:

```bash
SDK_MIGRATIONS="$(uv run python -c 'import gcl_sdk; print(gcl_sdk.__path__[0] + "/migrations")')"
uv run ra-apply-migration --config-file /etc/exordos_usage/exordos_usage.conf --path "$SDK_MIGRATIONS"
uv run ra-apply-migration --config-file /etc/exordos_usage/exordos_usage.conf --path migrations
uv run exordos-usage-user-api --config-file /etc/exordos_usage/exordos_usage.conf --config-dir /etc/exordos_usage/exordos_usage.d
uv run exordos-usage-gservice --config-file /etc/exordos_usage/exordos_usage.conf --config-dir /etc/exordos_usage/exordos_usage.d
```

Migrations use `MigrationStep`, stable UUIDs, dependencies and
`upgrade`/`downgrade`; RestAlchemy records applied steps in `ra_migrations`.
The element's configuration hook runs `bootstrap.sh` to apply both sets of
migrations before restarting the systemd services.

## Stored data

- `CollectorState` stores one collector checkpoint and the last ownership
  observation time.
- `UsageBinding` stores resource, realm and project ownership for a validity
  period, identified by UUID.
- `UsageInterval` references a binding and stores one metric's integrated
  quantity, measured coverage and calculation version for a period.

When ownership changes, the previous binding is closed and a new one is
created. Historical consumption remains attached to its original owner.

## API

`GET /v1/usage/` lists the available reports:

| Endpoint | Result | Additional parameters |
| --- | --- | --- |
| `/v1/usage/current/` | Current CPU, memory and root filesystem utilization | Required `realm_uuid` |
| `/v1/usage/summary/` | Totals by metric | None |
| `/v1/usage/timeseries/` | UTC time buckets | `step=5m`, `1h` or `1d`; default `1h` |
| `/v1/usage/breakdown/` | Totals by realm or resource | `group_by=realm` or `resource`; default `realm` |

Reports require a bearer token with `usage.interval.read` and a UTC range
`[start, end)` of at most 31 days, with boundaries on five-minute marks.
The project comes from the caller's IAM token; an unscoped token must supply
`project_id`. Optional filters are `realm_uuid`, `resource_uuid` and `metric`.
Supported metrics are `cpu_allocated`, `ram_allocated`, `disk_allocated`,
`cpu_used` and `ram_used`. Omitting `metric` returns all five.

Time series are limited to 2000 buckets. Breakdown pagination uses
`page_limit` (1 to 200 groups, default 100) and `page_marker`. When another
page exists, the response contains `X-Pagination-Marker`; pass its value as
`page_marker` with the same filters and grouping for the next page.

Quantities are exact decimal strings. Missing measurements have a null
quantity; measured zero remains zero. Each metric reports `covered_ms`,
`expected_ms` and `status` (`complete`, `partial` or `no_data`). Coverage is
summed across resources and refers only to observed ownership, including
windows not yet processed. Quantities sum stored segments fully inside the
requested range.

`GET /health/` checks service availability without IAM authentication.
Generate the OpenAPI artifact with `uv run exordos-usage-openapi-spec`; the
output is `docs/openapi/openapi_user.yaml` and is not committed.

## Collection semantics and current scope

- CPU is stored in vCPU-seconds; RAM and disk in byte-seconds.
- Five-minute windows are collected with a delay and retried after failures.
- Missing samples remain incomplete. Values are integrated only between
  neighboring finite samples within the configured maximum gap.
- Resizes are measured to scrape resolution. Attribution begins when observed;
  current ownership is never assigned to earlier consumption.
- Reprocessing a window replaces its records atomically with cursor progress.
- The current scope is the VM hosting each managed realm. Resources inside
  child realms, a user interface and financial charges are not implemented.

### Current realm utilization

```http
GET /v1/usage/current/?realm_uuid=07808b78-3a77-42f2-93ed-ca9dedbac02c
Authorization: Bearer <project-token>
```

The endpoint uses the same `usage.interval.read` permission and project scope
as reports. An unscoped token must also specify `project_id`. Only an active
realm binding in that project can be queried; otherwise the response is 404.
Before reading live metrics, usage checks the realm owner and node against
ecosystem. A changed owner or node returns 404; failed ownership verification
returns 503 without querying VictoriaMetrics.

CPU utilization is averaged over the last minute. Disk usage describes the root
filesystem `/`: used bytes are size minus free bytes, while available bytes
exclude reserved space. Memory usage is
`MemTotal - MemAvailable`. `cpu.total_cores` and `memory.total_bytes` describe
what the guest OS sees; usable RAM may be below the allocated VM capacity.
Node UUIDs resolve to node exporter instances through the `hostname` label of
`exordos_compute_node_info`. Node exporter metrics must have `job=node_exporter`
and `instance` equal to that hostname.

```json
{
  "project_id": "3f72c68d-5a5d-40a0-ac8a-9747719f7da9",
  "realm_uuid": "07808b78-3a77-42f2-93ed-ca9dedbac02c",
  "resource_uuid": "09dd50f1-ed1d-4dcf-94cf-df22284c9686",
  "observed_at": "2026-10-23T08:00:00Z",
  "status": "available",
  "cpu": {"total_cores": 4, "used_cores": 1.68, "utilization_percent": 42},
  "memory": {"total_bytes": 8589934592, "used_bytes": 6012954214, "utilization_percent": 70},
  "disk": {"mountpoint": "/", "total_bytes": 53687091200, "used_bytes": 21474836480, "available_bytes": 29527900160, "utilization_percent": 40}
}
```

Samples older than 120 seconds are excluded. A missing CPU, memory or disk
measurement is `null`; `status` is `partial` if some are available and
`no_data` if all are missing. Actual zero utilization remains zero.
`observed_at` is the snapshot evaluation time. Upstream failures return 503.
Live measurements are read from VictoriaMetrics and are not stored as billing
intervals.

### Nightly recovery

`exordos-usage-recovery.timer` runs a separate one-shot worker daily at
03:00 UTC. `Persistent=true` runs a missed activation after the timer restarts.
The main collector handles forward collection only.

The recovery worker searches the last seven days for missing metrics,
incomplete intervals and entirely missing windows within recorded ownership.
It preserves complete intervals and the forward collection checkpoint. Its
VictoriaMetrics requests run outside the collector's database lock. Failed
windows do not block later ones; they are retried on the next daily run.
The journal summary reports repaired, incomplete, failed and skipped windows.
Request failures make the service exit unsuccessfully; missing source samples
remain incomplete. Neither case causes continuous retries.

Set `[recovery] lookback_days` to change the horizon; keep it within the source
retention (currently 30 days on stage). Periods without recorded ownership
are not attributed retrospectively. Recovering them needs authoritative
historical ownership events.

```bash
systemctl list-timers exordos-usage-recovery.timer
systemctl start exordos-usage-recovery.service
journalctl -u exordos-usage-recovery.service
```
