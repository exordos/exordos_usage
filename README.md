# Exordos Usage

The `usage` element collects allocated CPU, RAM and disk capacity for managed
realm VMs from VictoriaMetrics. It stores interval consumption and resource
ownership history in PostgreSQL and exposes project-scoped statistics.

Realm ownership comes from the existing ecosystem `/v1/realms/` API. The
collector's dedicated role has only `exordos_ecosystem.realm.read`.
Financial accounts, tariffs and balance operations remain in ecosystem.

## Project structure

```text
exordos_usage/
    cmd/                 Service entry points
    common/              Python configuration options, clients and storage
    services/            Collection worker
    user_api/api/        RestAlchemy application, routes and controllers
    user_api/dm/         Attribution history and usage calculations
    tests/unit/          Tests without external services
    tests/functional/    PostgreSQL and authenticated API tests
migrations/              RestAlchemy migration steps
etc/exordos_usage/        Service configuration template and logging YAML
etc/systemd/             User API and collector services
exordos/                 Element manifest and image scripts
```

## Development

```bash
uv sync --extra test
uv run pytest exordos_usage/tests/unit
uv run ruff check .
uv run ruff format --check .
```

Run PostgreSQL tests against a disposable database. Each test uses a separate
schema and removes it afterward.

```bash
USAGE_TEST_DSN=postgresql://localhost/usage_test uv run pytest exordos_usage/tests/functional
```

## Configuration and startup

Options are declared in Python with `oslo.config` in
`exordos_usage/common/config.py`, following `exordos_db`.
Render `etc/exordos_usage/exordos_usage.conf.j2` into
`/etc/exordos_usage/exordos_usage.conf` and place `logging.yaml` beside it.
Keep the configuration private: it contains service and database credentials.

Configure the database URL, VictoriaMetrics endpoint and reader credentials,
the exact Core exporter `instance`, the ecosystem service credentials, a
stable source UUID, and the IAM client endpoint, audience and JWKS key.
An empty `ecosystem.host_realm` selects the central host realm.

Apply migrations before starting either service:

```bash
uv run ra-apply-migration --config-file /etc/exordos_usage/exordos_usage.conf --path migrations
uv run exordos-usage-user-api --config-file /etc/exordos_usage/exordos_usage.conf
uv run exordos-usage-gservice --config-file /etc/exordos_usage/exordos_usage.conf
```

Migrations use `MigrationStep`, stable UUIDs, dependencies and
`upgrade`/`downgrade`; RestAlchemy records applied steps in `ra_migrations`.
The element's configuration hook runs `cp_bootstrap.sh` to apply migrations
before restarting the systemd services.

## API

`GET /v1/usage/` lists the report endpoints: `summary/` for totals,
`timeseries/` for graphs and `breakdown/` for realm or resource groups.
Reports require `start`, `end` and the `usage.interval.read` permission.
The project comes from the caller's IAM token; an unscoped token must supply
`project_id`. Optional filters are `realm_uuid`, `resource_uuid` and `metric`.

Reports sum stored segments fully inside the requested range, up to 31 days.
Expected coverage comes from observed ownership, including unprocessed windows.
Missing measurements have a null quantity; measured zero remains zero.
`/health/` checks service availability without IAM authentication.

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

## Element build

```bash
exordos build -f
```

Dependencies are Core, observability, ecosystem and the communal PostgreSQL
cluster. The manifest creates a dedicated database and collector user. Set the
`usage_core_exporter_instance` Variable Store value for this installation.
The observability reader must allow `/api/v1/export`; ecosystem must export
its existing `realm.read` permission. Grant `usage.interval.read` to the roles
that should view statistics in their projects.

## GitHub configuration

The workflows follow `exordos_db`: unit tests and Ruff, element image build
and publication, Markdown linting, and Dependabot updates. PostgreSQL/API
tests also run against a PostgreSQL service in the tests workflow.

The build workflow requires a runner labelled `self-hosted` and `vm`, and
the `PUSH_CFG` repository secret containing the base64-encoded Exordos push
configuration. CODEOWNERS uses `@exordos/code_owners`.
