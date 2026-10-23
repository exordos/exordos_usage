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

import os
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from uuid import uuid4

import psycopg
import pytest
from restalchemy.storage.sql import engines, migrations

ROOT = Path(__file__).parents[2]


@pytest.fixture
def database_dsn():
    base = os.environ.get("USAGE_TEST_DSN")
    if not base:
        pytest.skip("Set USAGE_TEST_DSN to a disposable PostgreSQL database")
    schema = "usage_test_" + uuid4().hex
    with psycopg.connect(base, client_encoding="UTF8") as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
        )
    parts = urlsplit(base)
    params = dict(parse_qsl(parts.query))
    params.update(options=f"-c search_path={schema}", client_encoding="UTF8")
    value = base.split("?", 1)[0] + "?" + urlencode(params, quote_via=quote)
    engines.engine_factory.configure_factory(
        value, config={"min_size": 1, "max_size": 2}
    )
    try:
        yield value
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(base, client_encoding="UTF8") as conn:
            conn.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    psycopg.sql.Identifier(schema)
                )
            )


@pytest.fixture
def migration_engine():
    return migrations.MigrationEngine(str(ROOT / "migrations"))


@pytest.fixture
def dsn(database_dsn, migration_engine):
    migration_engine.apply_migration(migration_engine.get_latest_migration())
    return database_dsn
