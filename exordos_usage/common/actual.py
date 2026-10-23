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

"""Integrate raw node exporter samples with historical node identity."""

from collections import defaultdict
from decimal import Decimal

from exordos_usage.user_api.dm.usage import Segments, same_sample, sample_value


def points(rows):
    result = {}
    for row in rows:
        for timestamp, value in zip(row["timestamps"], row["values"]):
            timestamp, value = int(timestamp), sample_value(value)
            if timestamp in result and not same_sample(result[timestamp], value):
                raise ValueError("Conflicting node exporter samples")
            result[timestamp] = value
    return result


def pairs(values, max_gap_ms):
    ordered = sorted(values)
    for left, right in zip(ordered, ordered[1:]):
        if right - left <= max_gap_ms:
            yield left, right


def actual_samples(identities, rows, max_gap_ms):
    identity_rows = defaultdict(list)
    for row in identities:
        identity_rows[(row["metric"]["node_uuid"], row["metric"]["hostname"])].append(
            row
        )
    ownership = []
    for (node, host), chunks in identity_rows.items():
        values = points(chunks)
        for left, right in pairs(values, max_gap_ms):
            if values[left] == values[right] == 1:
                ownership.append((node, host, left, right))
    tracks = defaultdict(list)
    for node, host, left, right in ownership:
        tracks[("node", node)].append((left, right))
        tracks[("host", host)].append((left, right))
    for segments in tracks.values():
        ordered = sorted(segments)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Ambiguous historical node exporter identity")

    series, labels = defaultdict(list), {}
    hosts = {host for _, host in identity_rows}
    for row in rows:
        label = row["metric"]
        host, name = label["instance"], label["__name__"]
        if host not in hosts or label.get("job") != "node_exporter":
            raise ValueError("Unexpected node exporter identity")
        if name == "node_cpu_seconds_total":
            if label.get("mode") != "idle":
                continue
            cpu = label["cpu"]
        elif name in {"node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes"}:
            cpu = None
        else:
            raise ValueError("Unexpected node exporter metric")
        key = host, name, cpu
        if key in labels and labels[key] != label:
            raise ValueError("Ambiguous node exporter series")
        labels[key] = label
        series[key].append(row)
    series = {key: points(chunks) for key, chunks in series.items()}
    measured = defaultdict(list)
    for host in hosts:
        cores = [
            values
            for (instance, name, _), values in series.items()
            if instance == host and name == "node_cpu_seconds_total"
        ]
        if cores:
            timestamps = set.union(*(set(core) for core in cores))
            for left, right in pairs(timestamps, max_gap_ms):
                if not all(left in core and right in core for core in cores):
                    continue
                duration = Decimal(right - left) / 1000
                if not all(
                    core[t].is_finite() and core[t] >= 0
                    for core in cores
                    for t in (left, right)
                ):
                    continue
                deltas = [core[right] - core[left] for core in cores]
                if not all(
                    core[left].is_finite()
                    and core[left] >= 0
                    and delta.is_finite()
                    and 0 <= delta <= duration
                    for core, delta in zip(cores, deltas)
                ):
                    # Counter reset, invalid data or CPU topology change: no estimate.
                    continue
                busy = sum((1 - delta / duration for delta in deltas), Decimal(0))
                measured[(host, "cpu_used")].append((left, right, busy))
        total = series.get((host, "node_memory_MemTotal_bytes", None), {})
        available = series.get((host, "node_memory_MemAvailable_bytes", None), {})
        for left, right in pairs(set(total) | set(available), max_gap_ms):
            if not all(t in total and t in available for t in (left, right)):
                continue
            if not all(
                total[t].is_finite()
                and available[t].is_finite()
                and total[t] > 0
                and 0 <= available[t] <= total[t]
                for t in (left, right)
            ):
                continue
            measured[(host, "ram_used")].append(
                (left, right, total[left] - available[left])
            )
    result = defaultdict(list)
    for node, host, start, end in ownership:
        for metric in ("cpu_used", "ram_used"):
            for left, right, value in measured[(host, metric)]:
                left, right = max(start, left), min(end, right)
                if left < right:
                    result[(node, metric)].append((left, right, value))
    return {key: Segments(values) for key, values in result.items()}
