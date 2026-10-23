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

from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from exordos_usage.common.clients import Victoria

NOW = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)


def row(value, **labels):
    return {"metric": labels, "value": [NOW.timestamp(), str(value)]}


def test_current_utilization_and_exact_node_selector():
    client = Victoria("http://vm", "reader", "secret")
    resource = uuid4()
    with patch.object(
        client,
        "query",
        side_effect=[
            [row(1, hostname='realm"one')],
            [row(0.58, cpu=str(i)) for i in range(4)],
            [row(8 * 1024**3)],
            [row(2.4 * 1024**3)],
        ],
    ) as query:
        result = client.current(resource, NOW)
    assert result["status"] == "available"
    assert result["cpu"]["total_cores"] == 4
    assert result["cpu"]["used_cores"] == pytest.approx(1.68)
    assert result["cpu"]["utilization_percent"] == pytest.approx(42)
    assert result["memory"]["utilization_percent"] == pytest.approx(70)
    expressions = [call.args[0] for call in query.call_args_list]
    assert str(resource) in expressions[0]
    assert 'instance="realm\\"one"' in expressions[1]
    assert "[1m]" in expressions[1]
    assert all(
        "timestamp(" in expression and "<= 120" in expression
        for expression in expressions
    )


def test_missing_or_stale_node_identity_does_not_query_other_nodes():
    client = Victoria("http://vm", "reader", "secret")
    with patch.object(client, "query", return_value=[]) as query:
        result = client.current(uuid4(), NOW)
    assert result["status"] == "no_data"
    assert result["cpu"] is result["memory"] is None
    query.assert_called_once()


@pytest.mark.parametrize(
    "cpu,total,available,status",
    [
        ([], 8, 2, "partial"),
        ([row("NaN", cpu="0")], None, None, "no_data"),
        ([row(1, cpu="0")], 8, 10, "partial"),
        ([row(1, cpu="0")], 8, 8, "available"),
    ],
)
def test_missing_invalid_and_zero_measurements(cpu, total, available, status):
    client = Victoria("http://vm", "reader", "secret")
    with patch.object(
        client,
        "query",
        side_effect=[
            [row(1, hostname="realm")],
            cpu,
            [row(total)] if total is not None else [],
            [row(available)] if available is not None else [],
        ],
    ):
        result = client.current(uuid4(), NOW)
    assert result["status"] == status
    if total == available == 8:
        assert result["cpu"]["used_cores"] == 0
        assert result["memory"]["used_bytes"] == 0


def test_ambiguous_exporters_are_rejected():
    client = Victoria("http://vm", "reader", "secret")
    with patch.object(
        client, "query", return_value=[row(1, hostname="a"), row(1, hostname="b")]
    ):
        with pytest.raises(ValueError, match="Ambiguous"):
            client.current(uuid4(), NOW)


def test_query_errors_are_not_missing_measurements():
    client = Victoria("http://vm", "reader", "secret")
    response = Mock()
    response.json.return_value = {"status": "error"}
    with patch("exordos_usage.common.clients.requests.get", return_value=response):
        with pytest.raises(ValueError, match="Invalid VictoriaMetrics"):
            client.query("up", NOW)
