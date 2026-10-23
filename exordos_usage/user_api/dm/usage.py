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

"""Allocation integration from raw samples, without query lookback filling."""

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


class UsageUnit(str, enum.Enum):
    VCPU_SECONDS = "vcpu_seconds"
    BYTE_SECONDS = "byte_seconds"


METRICS = {
    "exordos_compute_node_cores": ("cpu_allocated", UsageUnit.VCPU_SECONDS.value),
    "exordos_compute_node_ram_bytes": ("ram_allocated", UsageUnit.BYTE_SECONDS.value),
    "exordos_compute_node_disk_bytes": ("disk_allocated", UsageUnit.BYTE_SECONDS.value),
}
CALCULATION_VERSION = 1


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("A timezone is required")
    return result.astimezone(timezone.utc)


def milliseconds(value: datetime) -> int:
    if value.utcoffset() is None:
        raise ValueError("A timezone is required")
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


@dataclass(frozen=True)
class Integral:
    quantity: Decimal
    covered_ms: int
    expected_ms: int

    @property
    def complete(self) -> bool:
        return self.covered_ms == self.expected_ms


def integrate(
    samples: list[tuple[int, Decimal]], start: int, end: int, max_gap_ms: int
) -> Integral:
    """Hold left values only between two finite samples within the gap bound.

    There is no unbounded extrapolation before the first or after the last
    sample. Missing time remains missing, rather than becoming zero usage.
    Resize timing is accurate only to the sample resolution.
    """
    if end <= start or max_gap_ms <= 0:
        raise ValueError("Invalid integration interval")
    unique = {}
    for timestamp, value in samples:
        if timestamp in unique and unique[timestamp] != value:
            raise ValueError("Conflicting samples at the same timestamp")
        unique[timestamp] = value
    points = sorted(unique.items())
    quantity, covered = Decimal(0), 0
    for (left, value), (right, next_value) in zip(points, points[1:]):
        if (
            right - left > max_gap_ms
            or not value.is_finite()
            or not next_value.is_finite()
            or value < 0
            or next_value < 0
        ):
            continue
        length = max(0, min(right, end) - max(left, start))
        quantity += value * Decimal(length) / 1000
        covered += length
    return Integral(quantity, covered, end - start)
