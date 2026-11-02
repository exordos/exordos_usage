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

from exordos_usage.common import openapi


def test_specification_describes_usage_catalog():
    spec = openapi.build()
    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["version"] == "latest"
    schema = spec["paths"]["/v1/usage/"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["type"] == "string"
    assert "/health/" in spec["paths"]


def test_ui_report_contracts_are_published():
    spec = openapi.build()
    for view in ("summary", "timeseries", "breakdown"):
        path = spec["paths"][f"/v1/usage/{view}/"]
        assert set(path) == {"get"}
        operation = path["get"]
        assert operation["security"] == [{"bearerAuth": []}]
        assert {p["name"] for p in operation["parameters"] if p.get("required")} == {
            "start",
            "end",
        }
        assert (
            operation["responses"]["200"]["content"]["application/json"]["schema"][
                "$ref"
            ].split("/")[-1]
            in spec["components"]["schemas"]
        )
    assert spec["components"]["schemas"]["UsageMetric"]["properties"]["quantity"][
        "nullable"
    ]
    assert (
        "X-Pagination-Marker"
        in spec["paths"]["/v1/usage/breakdown/"]["get"]["responses"]["200"]["headers"]
    )


def test_current_utilization_contract_is_published():
    spec = openapi.build()
    operation = spec["paths"]["/v1/usage/current/"]["get"]
    assert operation["security"] == [{"bearerAuth": []}]
    assert {p["name"] for p in operation["parameters"] if p.get("required")} == {
        "realm_uuid"
    }
    assert "503" in operation["responses"]
    assert spec["components"]["schemas"]["UsageCurrent"]["properties"]["cpu"][
        "nullable"
    ]


def test_actual_usage_metrics_are_published():
    spec = openapi.build()
    metric = spec["components"]["schemas"]["UsageMetric"]["properties"]["metric"]
    assert {"cpu_used", "ram_used"} <= set(metric["enum"])
    for view in ("summary", "timeseries", "breakdown"):
        params = spec["paths"][f"/v1/usage/{view}/"]["get"]["parameters"]
        selected = next(p for p in params if p["name"] == "metric")
        assert {"cpu_used", "ram_used"} <= set(selected["schema"]["enum"])


def test_hypervisor_current_contract():
    spec = openapi.build()
    operation = spec["paths"]["/v1/usage/hypervisors/current/"]["get"]
    assert [p["name"] for p in operation["parameters"]] == ["hypervisor_uuid"]
    assert operation["parameters"][0]["required"]
    assert {"403", "404", "422", "503"} <= operation["responses"].keys()
    properties = spec["components"]["schemas"]["HypervisorUsageCurrent"]["properties"]
    assert "hypervisor_uuid" in properties
    assert "realm_uuid" not in properties
    assert "project_id" not in properties


def test_node_current_contract():
    spec = openapi.build()
    operation = spec["paths"]["/v1/usage/nodes/current/"]["get"]
    assert [p["name"] for p in operation["parameters"]] == ["node_uuid"]
    assert "422" not in operation["responses"]
    properties = spec["components"]["schemas"]["NodeUsageCurrent"]["properties"]
    assert "node_uuid" in properties
    assert "hypervisor_uuid" not in properties


def test_compute_usage_uses_core_access_not_history_permission():
    spec = openapi.build()
    for collection in ("nodes", "hypervisors"):
        operation = spec["paths"][f"/v1/usage/{collection}/current/"]["get"]
        assert "usage.interval.read" not in operation["description"]
        assert f"/v1/compute/{collection}/" in operation["description"]
    assert (
        "usage.interval.read"
        in spec["paths"]["/v1/usage/summary/"]["get"]["description"]
    )
    assert (
        "usage.interval.read"
        in spec["paths"]["/v1/usage/current/"]["get"]["description"]
    )
