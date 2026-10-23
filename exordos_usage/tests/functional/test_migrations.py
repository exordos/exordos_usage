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

import subprocess
import sys
from pathlib import Path

import pytest
from psycopg.errors import CheckViolation
from restalchemy.common import contexts

ROOT = Path(__file__).parents[3]


def test_upgrade_is_repeatable_and_uses_restalchemy_registry(dsn, migration_engine):
    migration_engine.apply_migration(migration_engine.get_latest_migration())
    with contexts.Context().session_manager() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS count FROM ra_migrations WHERE applied"
            ).fetchone()["count"]
            == 1
        )
        assert (
            conn.execute("SELECT to_regclass('usage_schema') AS legacy").fetchone()[
                "legacy"
            ]
            is None
        )
        for table in ("usage_collector_state", "usage_bindings", "usage_intervals"):
            assert (
                conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()[
                    "name"
                ]
                == table
            )


def test_downgrade_and_reapply(database_dsn, migration_engine):
    head = migration_engine.get_latest_migration()
    migration_engine.apply_migration(head)
    migration_engine.rollback_migration(head)
    with contexts.Context().session_manager() as conn:
        assert (
            conn.execute(
                "SELECT to_regclass('usage_collector_state') AS name"
            ).fetchone()["name"]
            is None
        )
        assert not conn.execute("SELECT applied FROM ra_migrations").fetchone()[
            "applied"
        ]
    migration_engine.apply_migration(head)
    with contexts.Context().session_manager() as conn:
        assert conn.execute("SELECT applied FROM ra_migrations").fetchone()["applied"]


def test_standard_migration_cli_uses_service_config(database_dsn, tmp_path):
    config = tmp_path / "exordos_usage.conf"
    config.write_text("[db]\nconnection_url = " + database_dsn + "\n")
    command = [
        sys.executable,
        "-m",
        "restalchemy.cmd.apply_migration",
        "--config-file",
        str(config),
        "--path",
        str(ROOT / "migrations"),
    ]
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    with contexts.Context().session_manager() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS count FROM ra_migrations WHERE applied"
            ).fetchone()["count"]
            == 1
        )


def test_single_collector_schema_has_no_source_column(dsn):
    with contexts.Context().session_manager() as session:
        columns = session.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema=current_schema()"
            " AND table_name IN ('usage_collector_state','usage_bindings','usage_intervals')"
        ).fetchall()
        assert all(row["column_name"] != "source_uuid" for row in columns)
    with pytest.raises(CheckViolation):
        with contexts.Context().session_manager() as session:
            session.execute(
                "INSERT INTO usage_collector_state(id,next_window) VALUES (2,now())"
            )
