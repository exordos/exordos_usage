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

from decimal import Decimal
from uuid import uuid4

import pytest

from exordos_usage.common.actual import actual_samples
from exordos_usage.user_api.dm.usage import integrate

NODE = str(uuid4())


def row(name, values, times=(0, 15000, 30000), **labels):
    return {
        "metric": {"__name__": name, **labels},
        "timestamps": list(times),
        "values": values,
    }


def identity(node=NODE, host="realm", times=(0, 15000, 30000)):
    return row(
        "exordos_compute_node_info",
        [1] * len(times),
        times,
        node_uuid=node,
        hostname=host,
    )


def measurements(times=(0, 15000, 30000)):
    return [
        row(
            "node_cpu_seconds_total",
            [100, 107.5, 115],
            times,
            job="node_exporter",
            instance="realm",
            cpu="0",
            mode="idle",
        ),
        row(
            "node_cpu_seconds_total",
            [200, 203.75, 207.5],
            times,
            job="node_exporter",
            instance="realm",
            cpu="1",
            mode="idle",
        ),
        row(
            "node_memory_MemTotal_bytes",
            [8 * 1024**3] * 3,
            times,
            job="node_exporter",
            instance="realm",
        ),
        row(
            "node_memory_MemAvailable_bytes",
            [3 * 1024**3] * 3,
            times,
            job="node_exporter",
            instance="realm",
        ),
    ]


def integral(samples, metric, start=0, end=30000):
    return integrate(samples.get((NODE, metric), []), start, end, 45000)


def test_actual_cpu_and_memory_are_integrated_independently_of_allocations():
    samples = actual_samples([identity()], measurements(), 45000)
    cpu, memory = integral(samples, "cpu_used"), integral(samples, "ram_used")
    assert cpu.quantity == Decimal("37.5")  # 1.25 busy cores for 30 seconds
    assert memory.quantity == 5 * 1024**3 * 30
    assert cpu.complete and memory.complete
    # Ownership changes inside a scrape interval split consumption proportionally.
    assert integral(samples, "cpu_used", 10000, 20000).quantity == Decimal("12.5")


def test_idle_cpu_and_unused_memory_are_real_zero_not_missing():
    rows = measurements()
    rows[0]["values"] = rows[1]["values"] = [0, 15, 30]
    rows[3]["values"] = rows[2]["values"]
    samples = actual_samples([identity()], rows, 45000)
    for metric in ("cpu_used", "ram_used"):
        value = integral(samples, metric)
        assert value.quantity == 0 and value.complete


def test_counter_reset_is_not_billed_and_does_not_hide_memory():
    rows = measurements()
    rows[0]["values"] = [100, 1, 8.5]
    samples = actual_samples([identity()], rows, 45000)
    cpu = integral(samples, "cpu_used")
    assert cpu.quantity == Decimal("18.75") and cpu.covered_ms == 15000
    assert not cpu.complete and integral(samples, "ram_used").complete


def test_scrape_gaps_are_not_interpolated():
    samples = actual_samples(
        [identity(times=(0, 15000, 90000))],
        measurements(times=(0, 15000, 90000)),
        45000,
    )
    assert integral(samples, "cpu_used", 0, 90000).covered_ms == 15000
    assert integral(samples, "ram_used", 0, 90000).covered_ms == 15000


def test_missing_core_and_memory_samples_leave_gaps():
    rows = measurements()
    rows[1]["timestamps"] = [0, 30000]
    rows[1]["values"] = [200, 207.5]
    rows[3]["timestamps"] = [0, 30000]
    rows[3]["values"] = [3 * 1024**3] * 2
    samples = actual_samples([identity()], rows, 45000)
    assert integral(samples, "cpu_used").covered_ms == 0
    assert integral(samples, "ram_used").covered_ms == 0


def test_absent_identity_never_attributes_measurements():
    assert actual_samples([], [], 45000) == {}
    samples = actual_samples([identity(times=(15000, 30000))], measurements(), 45000)
    assert integral(samples, "cpu_used").covered_ms == 15000


def test_hostname_reuse_is_split_using_historical_identity():
    other = str(uuid4())
    samples = actual_samples(
        [identity(times=(0, 15000)), identity(node=other, times=(15000, 30000))],
        measurements(),
        45000,
    )
    assert integral(samples, "cpu_used").quantity == Decimal("18.75")
    assert integrate(samples[(other, "cpu_used")], 0, 30000, 45000).quantity == Decimal(
        "18.75"
    )


def test_overlapping_node_identity_is_rejected():
    with pytest.raises(ValueError, match="identity"):
        actual_samples([identity(), identity(node=str(uuid4()))], measurements(), 45000)


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", -1, 9 * 1024**3])
def test_invalid_memory_is_not_billed(value):
    rows = measurements()
    rows[3]["values"][1] = value
    samples = actual_samples([identity()], rows, 45000)
    assert integral(samples, "ram_used").covered_ms == 0


def test_conflicting_duplicate_scrapes_are_rejected():
    rows = measurements()
    duplicate = {**rows[0], "values": [1, 2, 3]}
    with pytest.raises(ValueError, match="Conflicting"):
        actual_samples([identity()], [*rows, duplicate], 45000)


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-Infinity", -1])
def test_invalid_cpu_counter_does_not_invent_consumption(value):
    rows = measurements()
    rows[0]["values"][1] = value
    samples = actual_samples([identity()], rows, 45000)
    assert integral(samples, "cpu_used").covered_ms == 0
