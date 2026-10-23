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

import json
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
import requests

from exordos_usage.common.clients import Ecosystem, Victoria


def test_export_keeps_raw_timestamps_and_merges_chunks():
    node = str(uuid4())
    row = {
        "metric": {"__name__": "exordos_compute_node_cores", "node_uuid": node},
        "timestamps": [1, 15001],
        "values": [4, 8],
    }
    response = Mock()
    response.iter_lines.return_value = [
        json.dumps(row).encode(),
        json.dumps(row).encode(),
    ]
    with patch("exordos_usage.common.clients.requests.get") as get:
        get.return_value.__enter__.return_value = response
        client = Victoria("http://vm", "reader", "secret")
        samples = client.samples(1, 300000, [node])
        assert (
            samples[(node, "exordos_compute_node_cores")]
            == [(1, Decimal(4)), (15001, Decimal(8))] * 2
        )
        assert get.call_args.args[0] == "http://vm/api/v1/export"
        assert get.call_args.kwargs["params"]["start"] == "0.001"
        assert 'node_uuid=~"' + node + '"' in get.call_args.kwargs["params"]["match[]"]


def test_failed_ownership_request_is_not_empty_snapshot():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError()
    with patch("exordos_usage.common.clients.requests.get", return_value=response):
        with pytest.raises(requests.HTTPError):
            Ecosystem("http://eco", Mock()).bindings()


def test_expired_service_token_is_refreshed_once():
    expired, current, auth = (
        Mock(status_code=401),
        Mock(status_code=200, headers={}),
        Mock(),
    )
    current.json.return_value = []
    with patch(
        "exordos_usage.common.clients.requests.get", side_effect=[expired, current]
    ):
        assert Ecosystem("http://eco", auth).bindings()[1] == []
        auth.authenticate.assert_called_once()


def test_ambiguous_ownership_is_rejected():
    item = realm()
    response = Mock(status_code=200, headers={})
    response.json.return_value = [item, item]
    with patch("exordos_usage.common.clients.requests.get", return_value=response):
        with pytest.raises(ValueError, match="Ambiguous"):
            Ecosystem("http://eco", Mock()).bindings()


def realm():
    return {
        "uuid": str(uuid4()),
        "node_uuid": str(uuid4()),
        "project_id": str(uuid4()),
        "host_realm": None,
        "kind": "MANAGED",
    }


def test_existing_realms_api_reads_all_pages():
    first, second = realm(), realm()
    page1 = Mock(status_code=200, headers={"X-Pagination-Marker": first["uuid"]})
    page1.json.return_value = [first]
    page2 = Mock(status_code=200, headers={})
    page2.json.return_value = [second]
    with patch(
        "exordos_usage.common.clients.requests.get", side_effect=[page1, page2]
    ) as get:
        observed, rows = Ecosystem("http://eco", Mock()).bindings()
        assert observed.utcoffset().total_seconds() == 0
        assert len(rows) == 2
        assert get.call_args.args[0] == "http://eco/v1/realms/"
        assert get.call_args.kwargs["params"]["page_marker"] == first["uuid"]
        assert set(rows[0]) == {
            "realm_uuid",
            "resource_uuid",
            "project_id",
            "source_realm_uuid",
        }


def test_failed_second_page_does_not_return_partial_snapshot():
    first = realm()
    response = Mock(status_code=200, headers={"X-Pagination-Marker": first["uuid"]})
    response.json.return_value = [first]
    with patch(
        "exordos_usage.common.clients.requests.get",
        side_effect=[response, requests.Timeout()],
    ):
        with pytest.raises(requests.Timeout):
            Ecosystem("http://eco", Mock()).bindings()


def test_repeated_page_marker_is_rejected():
    marker = str(uuid4())
    response = Mock(status_code=200, headers={"X-Pagination-Marker": marker})
    response.json.return_value = []
    with patch("exordos_usage.common.clients.requests.get", return_value=response):
        with pytest.raises(ValueError, match="Repeated"):
            Ecosystem("http://eco", Mock()).bindings()


def test_export_without_bindings_does_not_query_victoria():
    with patch("exordos_usage.common.clients.requests.get") as get:
        assert Victoria("http://vm", "reader", "secret").samples(0, 1, []) == {}
        get.assert_not_called()


def test_realms_without_host_realm_use_central_host():
    item = realm()
    del item["host_realm"]
    response = Mock(status_code=200, headers={})
    response.json.return_value = [item]
    with patch("exordos_usage.common.clients.requests.get", return_value=response):
        assert (
            Ecosystem("http://eco", Mock()).bindings()[1][0]["source_realm_uuid"]
            is None
        )


def test_export_collects_actual_samples_using_historical_hostname():
    from exordos_usage.tests.unit.test_actual import NODE, identity, measurements
    from exordos_usage.user_api.dm.usage import integrate

    client = Victoria("http://vm", "reader", "secret")
    with patch.object(
        client, "export", side_effect=[[identity()], measurements()]
    ) as export:
        samples = client.samples(0, 30000, [NODE])
    assert integrate(samples[(NODE, "cpu_used")], 0, 30000, 45000).quantity == Decimal(
        "37.5"
    )
    assert "exordos_compute_node_info" in export.call_args_list[0].args[0]
    assert 'instance=~"realm"' in export.call_args_list[1].args[0][0]
    assert 'mode="idle"' in export.call_args_list[1].args[0][0]


def test_failed_actual_export_is_not_silently_saved_as_missing():
    from exordos_usage.tests.unit.test_actual import NODE, identity

    client = Victoria("http://vm", "reader", "secret")
    with patch.object(client, "export", side_effect=[[identity()], requests.Timeout()]):
        with pytest.raises(requests.Timeout):
            client.samples(0, 30000, [NODE])


def test_null_export_samples_preserve_missing_coverage_without_blocking_collection():
    from exordos_usage.user_api.dm.usage import integrate

    node = str(uuid4())
    row = {
        "metric": {"__name__": "exordos_compute_node_cores", "node_uuid": node},
        "timestamps": [0, 15000, 30000, 45000],
        "values": [4, None, 4, 4],
    }
    client = Victoria("http://vm", "reader", "secret")
    with patch.object(client, "export", return_value=[row, row]):
        samples = client.samples(0, 45000, [node])
    measured = integrate(samples[(node, "exordos_compute_node_cores")], 0, 45000, 45000)
    assert measured.quantity == 60
    assert measured.covered_ms == 15000 and not measured.complete
