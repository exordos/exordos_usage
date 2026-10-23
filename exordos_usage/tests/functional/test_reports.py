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
from uuid import UUID, uuid4

import pytest
from restalchemy.common import contexts
from webob import Request

from exordos_usage.tests.functional.test_api import authenticated_app
from exordos_usage.user_api.dm.models import Store
from exordos_usage.user_api.dm.reports import read_report
from exordos_usage.user_api.dm.usage import METRICS, milliseconds, utc

START = utc("2026-10-22T10:00:00Z")
END = START + timedelta(minutes=10)


def binding(project, resource=None, realm=None):
    return {
        "project_id": str(project),
        "resource_uuid": str(resource or uuid4()),
        "realm_uuid": str(realm or uuid4()),
    }


def write_window(session, store, b, start=START, value=4):
    end = start + timedelta(minutes=5)
    samples = {
        (b["resource_uuid"], raw): [
            (milliseconds(start), Decimal(value)),
            (milliseconds(end), Decimal(value)),
        ]
        for raw in METRICS
    }
    store.save_window(start, end, samples, 300000)


def read(session, project, view="summary", **kwargs):
    return read_report(session, project, START, END, view=view, **kwargs)


def cpu(items):
    return next(m for m in items if m["metric"] == "cpu_allocated")


def test_views_agree_and_unprocessed_windows_are_missing(dsn):
    project = uuid4()
    b = binding(project)
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, [b])
        write_window(session, store, b)
        summary, _ = read(session, project)
        series, _ = read(session, project, "timeseries", step="5m")
        breakdown, _ = read(session, project, "breakdown")
    assert cpu(summary["items"]) == cpu(breakdown["items"][0]["metrics"])
    total = cpu(summary["items"])
    assert (
        Decimal(total["quantity"])
        == sum(
            Decimal(cpu(bucket["metrics"])["quantity"] or "0")
            for bucket in series["items"]
        )
        == 1200
    )
    assert total["expected_ms"] == 600000
    assert total["covered_ms"] == 300000
    assert total["status"] == "partial"
    assert cpu(series["items"][0]["metrics"])["status"] == "complete"
    missing = cpu(series["items"][1]["metrics"])
    assert missing["quantity"] is None and missing["status"] == "no_data"
    assert missing["expected_ms"] == 300000


def test_measured_zero_is_not_missing_and_coverage_sums_resources(dsn):
    project = uuid4()
    bindings = [binding(project), binding(project)]
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, bindings)
        samples = {
            (b["resource_uuid"], raw): [
                (milliseconds(START), Decimal(0)),
                (milliseconds(START) + 300000, Decimal(0)),
            ]
            for b in bindings
            for raw in METRICS
        }
        store.save_window(START, START + timedelta(minutes=5), samples, 300000)
        result, _ = read_report(
            session, project, START, START + timedelta(minutes=5), view="summary"
        )
    metric = cpu(result["items"])
    assert Decimal(metric["quantity"]) == 0
    assert metric["status"] == "complete"
    assert metric["expected_ms"] == metric["covered_ms"] == 600000


def test_project_ownership_history_and_filters(dsn):
    first, second = uuid4(), uuid4()
    b = binding(first)
    changed = {**b, "project_id": str(second)}
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, [b])
        store.observe(
            START + timedelta(seconds=150), [changed], max_observation_gap=180
        )
        write_window(session, store, b)
        for project in (first, second):
            result, _ = read_report(
                session,
                project,
                START,
                START + timedelta(minutes=5),
                view="summary",
                resource=UUID(b["resource_uuid"]),
                metric="cpu_allocated",
            )
            assert len(result["items"]) == 1
            assert Decimal(cpu(result["items"])["quantity"]) == 600
            other, _ = read(session, project, realm=uuid4())
            assert cpu(other["items"])["quantity"] is None


def test_empty_buckets_and_daily_utc_boundaries(dsn):
    with contexts.Context().session_manager() as session:
        series, _ = read(session, uuid4(), "timeseries", step="5m")
        daily, _ = read_report(
            session,
            uuid4(),
            utc("2026-10-22T23:55:00Z"),
            utc("2026-10-23T00:05:00Z"),
            view="timeseries",
            step="1d",
        )
    assert len(series["items"]) == 2
    assert all(cpu(b["metrics"])["quantity"] is None for b in series["items"])
    assert len(daily["items"]) == 2
    assert daily["items"][0]["end"] == utc("2026-10-23T00:00:00Z")
    assert daily["items"][1]["start"] == utc("2026-10-23T00:00:00Z")


def test_api_breakdown_paginates_groups_and_keeps_all_metrics(dsn):
    project = uuid4()
    bindings = [binding(project, realm=UUID(int=n)) for n in (1, 2, 3)]
    with contexts.Context().session_manager() as session:
        Store(session, 300).observe(START, bindings)
    app, token = authenticated_app(project)
    path = "/v1/usage/breakdown/?start=2026-10-22T10:00:00Z&end=2026-10-22T10:10:00Z&page_limit=2"

    def request(url):
        return Request.blank(
            url, headers={"Authorization": "Bearer " + token}
        ).get_response(app)

    first = request(path)
    assert first.status_int == 200, first.text
    assert [item["realm_uuid"] for item in first.json["items"]] == [
        str(UUID(int=n)) for n in (1, 2)
    ]
    assert all(len(item["metrics"]) == 5 for item in first.json["items"])
    marker = first.headers["X-Pagination-Marker"]
    second = request(path + "&page_marker=" + marker)
    assert second.status_int == 200
    assert [item["realm_uuid"] for item in second.json["items"]] == [str(UUID(int=3))]
    assert "X-Pagination-Marker" not in second.headers


@pytest.mark.parametrize("view", ["summary", "timeseries", "breakdown"])
@pytest.mark.parametrize(
    "scenario,status",
    [("own", 200), ("other", 403), ("no_permission", 403), ("expired", 401)],
)
def test_report_endpoints_enforce_iam(dsn, view, scenario, status):
    project = uuid4()
    app, token = authenticated_app(project, scenario)
    target = uuid4() if scenario == "other" else project
    url = f"/v1/usage/{view}/?start=2026-10-22T10:00:00Z&end=2026-10-22T10:10:00Z&project_id={target}"
    response = Request.blank(
        url, headers={"Authorization": "Bearer " + token}
    ).get_response(app)
    assert response.status_int == status, response.text


@pytest.mark.parametrize(
    "view,params",
    [
        ("summary", "start=bad&end=2026-10-22T10:10:00Z"),
        ("summary", "start=2026-10-22T10:00:00&end=2026-10-22T10:10:00Z"),
        ("summary", "start=2026-10-22T10:00:00.000001Z&end=2026-10-22T10:10:00Z"),
        ("summary", "start=2026-10-22T10:01:00Z&end=2026-10-22T10:10:00Z"),
        ("summary", "start=2026-10-22T10:00:00Z&end=2026-10-22T10:00:00Z"),
        ("summary", "start=2026-09-01T00:00:00Z&end=2026-10-22T10:10:00Z"),
        ("timeseries", "step=5m&start=2026-10-01T00:00:00Z&end=2026-10-22T00:00:00Z"),
        ("timeseries", "step=invalid"),
        ("breakdown", "group_by=project"),
        ("breakdown", "page_limit=0"),
        ("breakdown", "page_limit=201"),
        ("breakdown", "page_marker=bad"),
        ("summary", "realm_uuid=bad"),
        ("summary", "metric=cpu"),
        ("summary", "metric="),
        ("summary", "unknown=1"),
        ("summary", "metric=cpu_allocated&metric=ram_allocated"),
    ],
)
def test_api_rejects_invalid_report_queries(dsn, view, params):
    app, token = authenticated_app(uuid4())
    base = (
        "start=2026-10-22T10:00:00Z&end=2026-10-22T10:10:00Z&"
        if "start=" not in params
        else ""
    )
    response = Request.blank(
        f"/v1/usage/{view}/?{base}{params}",
        headers={"Authorization": "Bearer " + token},
    ).get_response(app)
    assert response.status_int == 400, response.text


def test_actual_consumption_is_stored_reported_and_recovered_separately(dsn):
    from exordos_usage.common.actual import actual_samples
    from exordos_usage.tests.unit.test_actual import identity, measurements

    project = uuid4()
    b = binding(project)
    start_ms = milliseconds(START)
    times = (start_ms, start_ms + 15000, start_ms + 30000)
    observed = actual_samples(
        [identity(node=b["resource_uuid"], times=times)],
        measurements(times=times),
        45000,
    )
    with contexts.Context().session_manager() as session:
        store = Store(session, 300)
        store.observe(START, [b])
        # Simulate an already-collected window predating actual usage collection.
        write_window(session, store, b)
        session.execute(
            "DELETE FROM usage_intervals WHERE metric IN ('cpu_used', 'ram_used')"
        )
        assert store.recovery_windows(START, START + timedelta(minutes=5)) == [START]
        store.save_window(
            START, START + timedelta(minutes=5), observed, 45000, repair=True
        )
        assert store.next_window() == START + timedelta(minutes=5)
        summary, _ = read(session, project)
        metrics = {m["metric"]: m for m in summary["items"]}
        assert Decimal(metrics["cpu_used"]["quantity"]) == Decimal("37.5")
        assert Decimal(metrics["ram_used"]["quantity"]) == 5 * 1024**3 * 30
        assert Decimal(metrics["cpu_allocated"]["quantity"]) == 1200
        assert metrics["cpu_used"]["covered_ms"] == 30000
        assert metrics["cpu_used"]["status"] == "partial"
        series, _ = read(session, project, "timeseries", step="5m", metric="cpu_used")
        assert Decimal(series["items"][0]["metrics"][0]["quantity"]) == Decimal("37.5")
        assert series["items"][1]["metrics"][0]["quantity"] is None
    app, token = authenticated_app(project)
    for metric in ("cpu_used", "ram_used"):
        url = f"/v1/usage/summary/?start=2026-10-22T10:00:00Z&end=2026-10-22T10:10:00Z&realm_uuid={b['realm_uuid']}&metric={metric}"
        response = Request.blank(
            url, headers={"Authorization": "Bearer " + token}
        ).get_response(app)
        assert response.status_int == 200, response.text
        assert response.json["items"][0] == metrics[metric]
