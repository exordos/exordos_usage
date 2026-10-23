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

from decimal import Decimal as D

import pytest

from exordos_usage.user_api.dm.usage import integrate, utc


def test_resize_and_irregular_scrapes_use_elapsed_time():
    result = integrate(
        [(0, D(4)), (10000, D(4)), (30000, D(8)), (60000, D(8))], 0, 60000, 45000
    )
    assert result.quantity == 360
    assert result.complete


def test_missing_time_is_not_zero_or_last_value_extrapolation():
    result = integrate([(0, D(4)), (15000, D(4)), (120000, D(4))], 0, 120000, 45000)
    assert result.quantity == 60
    assert result.covered_ms == 15000
    assert not result.complete


def test_single_sample_cannot_establish_interval_consumption():
    assert integrate([(0, D(4))], 0, 300000, 45000).covered_ms == 0


def test_equal_duplicates_and_chunks_are_not_counted_twice():
    assert (
        integrate([(10000, D(4)), (0, D(4)), (10000, D(4))], 0, 10000, 45000).quantity
        == 40
    )


def test_conflicting_duplicate_producers_fail_closed():
    with pytest.raises(ValueError, match="Conflicting"):
        integrate([(0, D(4)), (0, D(8))], 0, 10000, 45000)


@pytest.mark.parametrize("invalid", [D("NaN"), D("Infinity"), D(-1)])
def test_invalid_sample_does_not_bridge_gap(invalid):
    result = integrate([(0, D(4)), (10000, invalid), (20000, D(4))], 0, 20000, 45000)
    assert result.covered_ms == 0


def test_ownership_boundary_clips_integral():
    result = integrate([(0, D(4)), (15000, D(4))], 5000, 10000, 45000)
    assert result.quantity == 20
    assert result.complete


def test_large_memory_preserves_fractional_precision():
    result = integrate([(0, D(2**60)), (1000, D(2**60))], 1, 1000, 45000)
    assert result.quantity == D(2**60) * D("0.999")


def test_naive_dates_are_rejected():
    with pytest.raises(ValueError):
        utc("2026-10-22T10:00:00")
