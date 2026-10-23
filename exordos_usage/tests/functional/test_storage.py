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

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from restalchemy.common import contexts
from restalchemy.dm import filters

from exordos_usage.user_api.dm.models import (
    Store,
    UsageBinding,
    UsageInterval,
)
from exordos_usage.user_api.dm.usage import milliseconds, utc

START = utc("2026-10-22T10:00:00Z")
END = START + timedelta(seconds=300)


def binding(project=None):
    return {
        "resource_uuid": str(uuid4()),
        "realm_uuid": str(uuid4()),
        "project_id": str(project or uuid4()),
    }


def samples(b, value=4):
    return {
        (b["resource_uuid"], "exordos_compute_node_cores"): [
            (milliseconds(START) + seconds * 1000, Decimal(value))
            for seconds in range(0, 301, 15)
        ]
    }


def intervals_for_project(session, project):
    return [
        interval
        for interval in UsageInterval.objects.get_all(session=session)
        if interval.binding.project_id == UUID(str(project))
    ]


def test_replay_is_idempotent(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.save_window(START, END, samples(b), 45000)
        store.save_window(START, END, samples(b), 45000)
        rows = intervals_for_project(conn, b["project_id"])
        cpu = next(row for row in rows if row.metric == "cpu_allocated")
        assert cpu.quantity == 1200
        assert cpu.complete
        assert len(rows) == 5
        assert store.next_window() == END


def test_changed_owner_splits_interval_and_isolates_projects(dsn):
    first = binding()
    second = {**first, "project_id": str(uuid4())}
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [first])
        store.observe(START + timedelta(seconds=150), [second], max_observation_gap=180)
        store.save_window(START, END, samples(first), 45000)
        for b in (first, second):
            rows = intervals_for_project(conn, b["project_id"])
            assert (
                next(r for r in rows if r.metric == "cpu_allocated")["quantity"] == 600
            )
        assert not intervals_for_project(conn, uuid4())


def test_observation_does_not_backdate_ownership(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START + timedelta(seconds=150), [b])
        store.save_window(START, END, samples(b), 45000)
        rows = intervals_for_project(conn, b["project_id"])
        assert next(r for r in rows if r.metric == "cpu_allocated")["quantity"] == 600


def test_observation_outage_is_not_continuous_ownership(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.observe(END, [b])
        store.save_window(START, END, samples(b), 45000)
        assert not intervals_for_project(conn, b["project_id"])


def test_failed_transaction_keeps_previous_data_and_cursor(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.save_window(START, END, samples(b), 45000)
    with pytest.raises(ValueError):
        with contexts.Context().session_manager() as conn:
            store = Store(conn, 300)
            conflict = samples(b)
            conflict[(b["resource_uuid"], "exordos_compute_node_cores")].append(
                (milliseconds(START), Decimal(8))
            )
            store.save_window(START, END, conflict, 45000)
    with contexts.Context().session_manager() as conn:
        assert Store(conn, 300).next_window() == END
        rows = intervals_for_project(conn, b["project_id"])
        assert len(rows) == 5
        assert (
            next(row for row in rows if row.metric == "cpu_allocated")["quantity"]
            == 1200
        )


def test_missing_metrics_record_incomplete_not_zero_complete(dsn):
    b = binding()
    with contexts.Context().session_manager() as conn:
        store = Store(conn, 300)
        store.observe(START, [b])
        store.save_window(START, END, {}, 45000)
        rows = intervals_for_project(conn, b["project_id"])
        assert all(r.quantity == 0 and not r.complete for r in rows)


def test_only_one_worker_locks_collector(dsn):

    def try_lock():
        with contexts.Context().session_manager() as session:
            return Store(session, 300).lock()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with contexts.Context().session_manager() as first:
            assert Store(first, 300).lock()
            assert not pool.submit(try_lock).result(timeout=5)
        assert pool.submit(try_lock).result(timeout=5)


def test_collector_state_is_singleton_and_survives_store_recreation(dsn):
    b = binding()
    with contexts.Context().session_manager() as session:
        first = Store(session, 300)
        first.observe(START, [b])
        first.save_window(START, END, samples(b), 45000)
    with contexts.Context().session_manager() as session:
        restarted = Store(session, 300)
        assert restarted.next_window() == END
        restarted.observe(END, [b], max_observation_gap=600)
        assert (
            session.execute(
                "SELECT count(*) AS count FROM usage_collector_state"
            ).fetchone()["count"]
            == 1
        )
        assert (
            session.execute(
                "SELECT observed_at FROM usage_collector_state WHERE id=1"
            ).fetchone()["observed_at"]
            == END
        )


def test_binding_is_generated_and_referenced_by_intervals(dsn):
    b = binding()
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, [b])
        store.save_window(START, END, samples(b), 45000)
        stored = UsageBinding.objects.get_one(session=session)
        assert isinstance(stored.uuid, UUID)
        intervals = UsageInterval.objects.get_all(session=session)
        assert len(intervals) == 5
        assert all(isinstance(interval.binding, UsageBinding) for interval in intervals)
        assert all(interval.binding.uuid == stored.uuid for interval in intervals)
        assert all(
            interval.binding.project_id == UUID(b["project_id"])
            for interval in intervals
        )
        assert stored.resource_uuid == UUID(b["resource_uuid"])


def test_interval_update_and_delete_are_scoped_to_binding(dsn):
    first, second = binding(), binding()
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, [first, second])
        store.save_window(START, END, {**samples(first), **samples(second)}, 45000)
        intervals = UsageInterval.objects.get_all(
            session=session, filters={"metric": filters.EQ("cpu_allocated")}
        )
        assert len(intervals) == 2
        selected, other = intervals
        assert "resource_uuid" not in selected.properties
        assert set(selected.get_id_properties()) == {
            "binding",
            "metric",
            "period_start",
            "period_end",
        }
        selected.quantity = Decimal(99)
        selected.update(session=session)
        unchanged = UsageInterval.objects.get_one(
            session=session,
            filters={
                "binding": filters.EQ(other.binding),
                "metric": filters.EQ("cpu_allocated"),
            },
        )
        assert unchanged.quantity == 1200
        selected.delete(session=session)
        remaining = UsageInterval.objects.get_all(
            session=session, filters={"metric": filters.EQ("cpu_allocated")}
        )
        assert len(remaining) == 1
        assert remaining[0].binding.uuid == other.binding.uuid
        columns = session.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema=current_schema() AND table_name='usage_intervals'"
        ).fetchall()
        assert all(row["column_name"] != "resource_uuid" for row in columns)
