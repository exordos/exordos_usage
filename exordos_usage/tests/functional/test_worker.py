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

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
import requests
from restalchemy.common import contexts

from exordos_usage.services.gservice import Worker
from exordos_usage.user_api.dm.models import Store, UsageInterval
from exordos_usage.user_api.dm.usage import milliseconds, utc

START = utc("2026-10-22T10:00:00Z")


def collector(dsn):
    conf = SimpleNamespace(
        db=SimpleNamespace(connection_url=dsn),
        worker=SimpleNamespace(
            window_seconds=300,
            delay_seconds=120,
            poll_seconds=60,
            gap_seconds=45,
        ),
        ecosystem=SimpleNamespace(
            endpoint="http://eco",
            core_endpoint="http://core",
            username="usage",
            password="secret",
            host_realm=None,
        ),
        victoria=SimpleNamespace(
            endpoint="http://vm",
            username="reader",
            password="secret",
        ),
    )
    worker = Worker(conf)
    worker.ecosystem, worker.victoria = Mock(), Mock()
    return worker


def test_failed_metrics_preserves_observation_and_does_not_advance_cursor(dsn):
    worker = collector(dsn)
    b = {
        "resource_uuid": str(uuid4()),
        "realm_uuid": str(uuid4()),
        "project_id": str(uuid4()),
        "source_realm_uuid": None,
    }
    worker.ecosystem.bindings.return_value = START, [b]
    worker.tick(now=START)
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=60), [b]
    worker.tick(now=START + timedelta(seconds=60))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=120), [b]
    worker.tick(now=START + timedelta(seconds=120))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=180), [b]
    worker.tick(now=START + timedelta(seconds=180))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=240), [b]
    worker.tick(now=START + timedelta(seconds=240))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=300), [b]
    worker.tick(now=START + timedelta(seconds=300))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=360), [b]
    worker.tick(now=START + timedelta(seconds=360))
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=420), [b]
    worker.victoria.samples.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        worker.tick(now=START + timedelta(seconds=420))
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        assert store.next_window() == START
        assert conn.execute("SELECT observed_at FROM usage_collector_state").fetchone()[
            "observed_at"
        ] == START + timedelta(seconds=420)
    worker.victoria.samples.side_effect = None
    worker.victoria.samples.return_value = {
        (b["resource_uuid"], raw): [
            (milliseconds(START) + second * 1000, Decimal(4))
            for second in range(0, 301, 15)
        ]
        for raw in (
            "exordos_compute_node_cores",
            "exordos_compute_node_ram_bytes",
            "exordos_compute_node_disk_bytes",
        )
    }
    worker.ecosystem.bindings.return_value = START + timedelta(seconds=480), [b]
    worker.tick(now=START + timedelta(seconds=480))
    with contexts.Context().session_manager() as conn:
        rows = UsageInterval.objects.get_all(session=conn)
        assert len(rows) == 3 and all(row.quantity == 1200 for row in rows)
        assert Store(conn, 300).next_window() == START + timedelta(seconds=300)


def test_failed_realm_read_never_opens_database():
    worker = collector("unused")
    worker.ecosystem.bindings.side_effect = requests.Timeout()
    with patch(
        "exordos_usage.services.gservice.contexts.Context.session_manager"
    ) as connect_mock:
        with pytest.raises(requests.Timeout):
            worker.tick()
        connect_mock.assert_not_called()


def test_collection_selects_historical_nodes_after_realm_removal(dsn):
    worker = collector(dsn)
    binding = {
        "resource_uuid": str(uuid4()),
        "realm_uuid": str(uuid4()),
        "project_id": str(uuid4()),
    }
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [binding], 120)
        store.observe(START + timedelta(seconds=60), [], 120)
        worker.victoria.samples.return_value = {}
        worker._collect(store, START, START + timedelta(seconds=300))
        assert worker.victoria.samples.call_args.args[2] == {
            UUID(binding["resource_uuid"])
        }


def test_forward_collector_does_not_retry_historical_incomplete_windows(dsn):
    worker = collector(dsn)
    b = {
        "resource_uuid": str(uuid4()),
        "realm_uuid": str(uuid4()),
        "project_id": str(uuid4()),
        "source_realm_uuid": None,
    }
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.observe(START + timedelta(hours=1), [b], max_observation_gap=7200)
        store.save_window(
            START + timedelta(minutes=55), START + timedelta(hours=1), {}, 45000
        )
    now = START + timedelta(hours=1, minutes=1)
    worker.ecosystem.bindings.return_value = now, [b]
    worker.tick(now=now)
    worker.victoria.samples.assert_not_called()
