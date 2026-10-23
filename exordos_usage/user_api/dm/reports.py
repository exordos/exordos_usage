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


"""UI aggregates over observed ownership and stored usage intervals."""

from exordos_usage.user_api.dm.usage import METRICS

STEPS = {"5m": 300, "1h": 3600, "1d": 86400}
METRIC_UNITS = dict(METRICS.values())


def metric_result(metric, quantity=None, covered=0, expected=0):
    return {
        "metric": metric,
        "unit": METRIC_UNITS[metric],
        "quantity": str(quantity) if covered else None,
        "covered_ms": int(covered),
        "expected_ms": int(expected),
        "status": "no_data"
        if not covered
        else ("complete" if covered == expected else "partial"),
    }


def read_report(
    session,
    project,
    start,
    end,
    *,
    view,
    realm=None,
    resource=None,
    metric=None,
    step="1h",
    group_by="realm",
    page_limit=100,
    marker=None,
):
    """Aggregate measurements and expected coverage in one database snapshot.

    Expected coverage comes from ownership, including intervals the worker has
    not processed yet. A missing window therefore cannot look complete. Coverage
    describes observed ownership; history before the first observation is unknown.
    """
    metrics = [metric] if metric else list(METRIC_UNITS)
    params = {
        "project": project,
        "start": start,
        "end": end,
        "step": f"{STEPS[step]} seconds",
        "realm": realm,
        "resource": resource,
        "metrics": metrics,
        "marker": marker,
        "limit": page_limit + 1,
    }
    selection = "b.project_id=%(project)s AND b.valid_from < %(end)s AND (b.valid_until IS NULL OR b.valid_until > %(start)s)"
    if realm:
        selection += " AND b.realm_uuid=%(realm)s"
    if resource:
        selection += " AND b.resource_uuid=%(resource)s"
    group_column = {"realm": "realm_uuid", "resource": "resource_uuid"}[group_by]
    grouping = f"b.{group_column}" if view == "breakdown" else "NULL::uuid"
    page = ""
    if view == "breakdown":
        page = f"""WHERE b.{group_column} IN (
            SELECT DISTINCT {group_column} FROM selected_bindings
            WHERE (%(marker)s::uuid IS NULL OR {group_column} > %(marker)s::uuid)
            ORDER BY {group_column} LIMIT %(limit)s
        )"""
    buckets = "SELECT %(start)s::timestamptz AS start, %(end)s::timestamptz AS finish"
    if view == "timeseries":
        buckets = """SELECT greatest(t, %(start)s) AS start,
            least(t + %(step)s::interval, %(end)s) AS finish
            FROM generate_series(
                date_bin(%(step)s::interval, %(start)s::timestamptz,
                         '1970-01-01 00:00:00+00'::timestamptz),
                %(end)s::timestamptz - interval '1 microsecond',
                %(step)s::interval
            ) t"""
    query = f"""
        WITH selected_bindings AS (
            SELECT b.* FROM usage_bindings b
            WHERE {selection}
        ), buckets AS ({buckets}), aggregated AS (
            SELECT {grouping} AS group_uuid, bucket.start, bucket.finish,
                   metric.metric,
                   sum(floor(extract(epoch FROM
                       least(coalesce(b.valid_until, bucket.finish), bucket.finish)
                   ) * 1000) - floor(extract(epoch FROM
                       greatest(b.valid_from, bucket.start)
                   ) * 1000)) AS expected_ms,
                   sum(measured.quantity) AS quantity,
                   coalesce(sum(measured.covered_ms), 0) AS covered_ms
            FROM selected_bindings b
            JOIN buckets bucket ON b.valid_from < bucket.finish
                AND (b.valid_until IS NULL OR b.valid_until > bucket.start)
            CROSS JOIN unnest(%(metrics)s::text[]) AS metric(metric)
            LEFT JOIN LATERAL (
                SELECT sum(i.quantity) AS quantity, sum(i.covered_ms) AS covered_ms
                FROM usage_intervals i
                WHERE i.binding=b.uuid AND i.metric=metric.metric
                  AND i.period_start >= bucket.start AND i.period_end <= bucket.finish
            ) measured ON TRUE
            {page}
            GROUP BY group_uuid, bucket.start, bucket.finish, metric.metric
        )
        SELECT bucket.start, bucket.finish,
               aggregated.group_uuid, aggregated.metric,
               aggregated.quantity, aggregated.covered_ms, aggregated.expected_ms
        FROM buckets bucket
        LEFT JOIN aggregated ON aggregated.start=bucket.start
            AND aggregated.finish=bucket.finish
        ORDER BY group_uuid, start, metric
    """
    rows = session.execute(query, params).fetchall()
    groups = {}
    bucket_ends = {}
    for row in rows:
        key = row["start"] if view == "timeseries" else row["group_uuid"]
        if view == "breakdown" and key is None:
            continue
        if key not in groups:
            groups[key] = {name: metric_result(name) for name in metrics}
        if row["metric"] is not None:
            groups[key][row["metric"]] = metric_result(
                row["metric"], row["quantity"], row["covered_ms"], row["expected_ms"]
            )
        bucket_ends[row["start"]] = row["finish"]

    next_marker = None
    if view == "summary":
        items = list(groups[None].values())
    elif view == "timeseries":
        items = [
            {"start": left, "end": bucket_ends[left], "metrics": list(values.values())}
            for left, values in groups.items()
        ]
    else:
        keys = list(groups)
        if len(keys) > page_limit:
            next_marker = str(keys[page_limit - 1])
        items = [
            {group_column: key, "metrics": list(groups[key].values())}
            for key in keys[:page_limit]
        ]
    return {"items": items}, next_marker
