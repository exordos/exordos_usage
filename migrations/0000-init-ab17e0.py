#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = []

    @property
    def migration_id(self):
        return "ab17e08a-e09a-4c17-a926-93c975838fd2"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """\
CREATE TABLE usage_collector_state (
    id integer PRIMARY KEY CHECK (id = 1),
    next_window timestamptz NOT NULL,
    observed_at timestamptz
);
""",
            """\
CREATE TABLE usage_bindings (
    uuid uuid PRIMARY KEY,
    resource_uuid uuid NOT NULL,
    realm_uuid uuid NOT NULL,
    project_id uuid,
    valid_from timestamptz NOT NULL,
    valid_until timestamptz,
    CHECK (valid_until IS NULL OR valid_until >= valid_from)
);
""",
            """\
CREATE UNIQUE INDEX usage_binding_current
    ON usage_bindings(resource_uuid) WHERE valid_until IS NULL;
""",
            """\
CREATE INDEX usage_binding_history
    ON usage_bindings(valid_from, valid_until);
""",
            """\
CREATE TABLE usage_intervals (
    binding uuid NOT NULL REFERENCES usage_bindings(uuid),
    window_start timestamptz NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    metric text NOT NULL,
    unit text NOT NULL,
    quantity numeric NOT NULL CHECK (quantity >= 0),
    covered_ms bigint NOT NULL CHECK (covered_ms >= 0),
    expected_ms bigint NOT NULL CHECK (expected_ms > 0),
    complete boolean NOT NULL,
    calculation_version integer NOT NULL,
    PRIMARY KEY (binding, metric, period_start, period_end),
    CHECK (period_end > period_start),
    CHECK (covered_ms <= expected_ms),
    CHECK (complete = (covered_ms = expected_ms))
);
""",
            """\
CREATE INDEX usage_interval_period ON usage_intervals(period_start, period_end);
CREATE INDEX usage_interval_window ON usage_intervals(binding, window_start);
""",
        ]
        expressions.append(
            "CREATE INDEX usage_binding_project ON usage_bindings(project_id, valid_from);"
        )
        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        for table in ("usage_intervals", "usage_bindings", "usage_collector_state"):
            self._delete_table_if_exists(session, table)


migration_step = MigrationStep()
