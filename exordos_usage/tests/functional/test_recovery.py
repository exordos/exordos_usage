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
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
import requests
from restalchemy.common import contexts

from exordos_usage.services.recovery import RecoveryWorker
from exordos_usage.user_api.dm.models import Store, UsageInterval
from exordos_usage.user_api.dm.usage import METRICS, milliseconds, utc

START = utc("2026-10-20T00:00:00Z")
WINDOW = timedelta(seconds=300)


def worker():
    conf = SimpleNamespace(
        worker=SimpleNamespace(window_seconds=300, gap_seconds=45),
        recovery=SimpleNamespace(lookback_days=7),
        victoria=SimpleNamespace(
            endpoint="http://vm", username="reader", password="secret"
        ),
    )
    result = RecoveryWorker(conf)
    result.victoria = Mock()
    return result


def binding():
    return {
        "resource_uuid": str(uuid4()),
        "realm_uuid": str(uuid4()),
        "project_id": str(uuid4()),
    }


def samples(b, start, value=4):
    return {
        (b["resource_uuid"], raw): [
            (milliseconds(start) + second * 1000, Decimal(value))
            for second in range(0, 301, 15)
        ]
        for raw in METRICS
    }


def prepare(b, windows=1):
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.observe(START + WINDOW * windows, [b], max_observation_gap=864000)
        for index in range(windows):
            start = START + WINDOW * index
            store.save_window(start, start + WINDOW, {}, 45000)


def test_recovers_old_missing_metrics_and_entire_windows_without_duplicates(dsn):
    b = binding()
    prepare(b, 2)
    with contexts.Context().session_manager() as conn:
        conn.execute(
            "DELETE FROM usage_intervals WHERE window_start = %s OR metric = 'ram_allocated'",
            (START,),
        )
    recovery = worker()
    recovery.victoria.samples.side_effect = [
        samples(b, START),
        samples(b, START + WINDOW),
    ]
    result = recovery.run_once(now=START + timedelta(days=2))
    assert result == {"repaired": 2, "incomplete": 0, "failed": 0, "skipped": 0}
    with contexts.Context().session_manager() as conn:
        rows = UsageInterval.objects.get_all(session=conn)
        assert len(rows) == 10 and all(
            row.complete and row.quantity == 1200 for row in rows
        )
        assert Store(conn, 300).next_window() == START + WINDOW * 2
    recovery.victoria.samples.reset_mock()
    assert recovery.run_once(now=START + timedelta(days=2))["repaired"] == 0
    recovery.victoria.samples.assert_not_called()


def test_repair_preserves_complete_values_even_when_source_changes(dsn):
    b = binding()
    prepare(b)
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        cpu = {
            key: points
            for key, points in samples(b, START).items()
            if key[1] == "exordos_compute_node_cores"
        }
        store.save_window(START, START + WINDOW, cpu, 45000)
    recovery = worker()
    recovery.victoria.samples.return_value = samples(b, START, value=8)
    assert recovery.run_once(now=START + WINDOW)["repaired"] == 1
    with contexts.Context().session_manager() as conn:
        rows = UsageInterval.objects.get_all(session=conn)
        assert (
            next(row.quantity for row in rows if row.metric == "cpu_allocated") == 1200
        )
        assert all(
            row.quantity == 2400 for row in rows if row.metric != "cpu_allocated"
        )


def test_failed_window_rolls_back_and_does_not_block_later_windows(dsn):
    b = binding()
    prepare(b, 2)
    recovery = worker()
    recovery.victoria.samples.side_effect = [
        requests.Timeout(),
        samples(b, START + WINDOW),
    ]
    result = recovery.run_once(now=START + WINDOW * 2)
    assert result["failed"] == result["repaired"] == 1
    assert recovery.failed
    with contexts.Context().session_manager() as conn:
        assert Store(conn, 300).recovery_windows(START, START + WINDOW * 2) == [START]
        assert Store(conn, 300).next_window() == START + WINDOW * 2


def test_missing_source_remains_incomplete_and_only_tried_once_per_run(dsn):
    b = binding()
    prepare(b)
    recovery = worker()
    recovery.victoria.samples.return_value = {}
    result = recovery.run_once(now=START + WINDOW)
    assert result["incomplete"] == 1
    recovery.victoria.samples.assert_called_once()
    assert not recovery.failed


def test_recovery_does_not_extend_beyond_checkpoint_or_lookback(dsn):
    b = binding()
    prepare(b)
    recovery = worker()
    assert recovery.run_once(now=START + timedelta(days=8))["repaired"] == 0
    recovery.victoria.samples.assert_not_called()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        assert store.recovery_windows(START, START + WINDOW * 3) == [START]
        with pytest.raises(ValueError, match="checkpoint"):
            store.save_window(
                START + WINDOW, START + WINDOW * 2, {}, 45000, repair=True
            )


def test_recovery_uses_deleted_realms_historical_binding(dsn):
    b = binding()
    prepare(b)
    with contexts.Context().session_manager() as conn:
        Store(conn, 300).observe(START + WINDOW + timedelta(seconds=60), [])
    recovery = worker()
    recovery.victoria.samples.return_value = samples(b, START)
    assert recovery.run_once(now=START + WINDOW * 2)["repaired"] == 1
    assert recovery.victoria.samples.call_args.args[2] == {UUID(b["resource_uuid"])}


def test_recovery_does_not_invent_ownership_across_collector_outage(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START + timedelta(seconds=30), [b])
        store.observe(START + WINDOW * 2, [b])
        store.save_window(START + WINDOW, START + WINDOW * 2, {}, 45000)
        assert store.recovery_windows(START, START + WINDOW * 2) == []


def test_fetch_does_not_hold_collector_lock(dsn):
    b = binding()
    prepare(b)
    recovery = worker()

    def fetch(*args, **kwargs):
        with contexts.Context().session_manager() as conn:
            assert Store(conn, 300).lock()
        return samples(b, START)

    recovery.victoria.samples.side_effect = fetch
    assert recovery.run_once(now=START + WINDOW)["repaired"] == 1


def test_rechecks_completed_window_before_saving(dsn):
    b = binding()
    prepare(b)
    recovery = worker()

    def fetch(*args, **kwargs):
        with contexts.Context().session_manager() as conn:
            Store(conn, 300).save_window(
                START, START + WINDOW, samples(b, START), 45000
            )
        return {}

    recovery.victoria.samples.side_effect = fetch
    assert recovery.run_once(now=START + WINDOW)["skipped"] == 1
    with contexts.Context().session_manager() as conn:
        assert all(row.complete for row in UsageInterval.objects.get_all(session=conn))
