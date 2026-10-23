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

"""Generate the user API specification from its RestAlchemy routes."""

import json

import webob
from restalchemy.api import applications, constants, contexts
from restalchemy.openapi import engines

from exordos_usage.user_api.dm.usage import UsageUnit


class UsageOpenApiEngine(engines.OpenApiEngine):
    """Describe the custom aggregate response, which has no RA resource model."""

    def build_openapi_specification(self, version, request):
        spec = super().build_openapi_specification(version, request)
        components = spec.setdefault("components", {})
        components.setdefault("securitySchemes", {})["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        components.setdefault("schemas", {}).setdefault("Error", {"type": "object"})
        health = spec["paths"]["/health/"]["get"]
        health["responses"] = dict(health["responses"])
        health["responses"]["200"] = {
            "description": "Service is running.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string", "enum": ["ok"]}},
                        "required": ["status"],
                    }
                }
            },
        }
        add_report_specs(spec)
        add_current_spec(spec)
        return spec


def build():
    from exordos_usage.user_api.api import app

    application = applications.OpenApiApplication(
        route_class=app.get_api_application(), openapi_engine=app.get_openapi_engine()
    )
    request = webob.Request.blank("/")
    request.application = application
    request.api_context = contexts.RequestContext(request)
    request.api_context.set_active_method(constants.GET)
    specification = application.openapi_engine.build_openapi_specification(
        version="3.0.3", request=request
    )
    specification["servers"][0]["url"] = "http://127.0.0.1:8080"
    specification["info"]["version"] = "latest"
    return json.loads(json.dumps(specification))


def add_report_specs(spec):
    """Publish the shared UI query contract and its three response shapes."""
    schemas = spec["components"]["schemas"]
    date = {"type": "string", "format": "date-time"}
    uuid = {"type": "string", "format": "uuid"}
    metric = {
        "type": "object",
        "required": [
            "metric",
            "unit",
            "quantity",
            "covered_ms",
            "expected_ms",
            "status",
        ],
        "properties": {
            "metric": {
                "type": "string",
                "enum": [
                    "cpu_allocated",
                    "ram_allocated",
                    "disk_allocated",
                    "cpu_used",
                    "ram_used",
                ],
            },
            "unit": {"type": "string", "enum": [unit.value for unit in UsageUnit]},
            "quantity": {
                "type": "string",
                "nullable": True,
                "description": "Exact decimal quantity; null when no samples cover the period. cpu_used is busy core-seconds from idle counter deltas; ram_used is (MemTotal minus MemAvailable) integrated in byte-seconds. Allocated metrics describe provisioned capacity, independently of actual usage.",
            },
            "covered_ms": {
                "type": "integer",
                "minimum": 0,
                "description": "Measured resource-milliseconds, summed across resources.",
            },
            "expected_ms": {
                "type": "integer",
                "minimum": 0,
                "description": "Expected resource-milliseconds from observed ownership, including unprocessed windows.",
            },
            "status": {"type": "string", "enum": ["complete", "partial", "no_data"]},
        },
    }
    schemas["UsageMetric"] = metric
    metrics = {"type": "array", "items": {"$ref": "#/components/schemas/UsageMetric"}}
    common = {
        "project_id": uuid,
        "start": date,
        "end": date,
    }
    parameters = [
        {
            "name": name,
            "in": "query",
            "required": name in {"start", "end"},
            "schema": schema,
            "description": description,
        }
        for name, schema, description in (
            (
                "start",
                date,
                "Inclusive start; timezone required; UTC five-minute boundary.",
            ),
            (
                "end",
                date,
                "Exclusive end; timezone required; UTC five-minute boundary. Range at most 31 days.",
            ),
            (
                "project_id",
                uuid,
                "Required for an unscoped token; otherwise inferred from the IAM token.",
            ),
            ("realm_uuid", uuid, "Filter by historical realm ownership."),
            ("resource_uuid", uuid, "Filter by resource UUID."),
            (
                "metric",
                metric["properties"]["metric"],
                "Return one metric; omitted returns all allocated and actual usage metrics.",
            ),
        )
    ]
    for view, title in (
        ("summary", "Summary"),
        ("timeseries", "Timeseries"),
        ("breakdown", "Breakdown"),
    ):
        props = dict(common)
        params = list(parameters)
        item_schema = metrics
        if view == "timeseries":
            props["step"] = {"type": "string", "enum": ["5m", "1h", "1d"]}
            params += [
                {
                    "name": "step",
                    "in": "query",
                    "schema": {**props["step"], "default": "1h"},
                    "description": "UTC buckets; at most 2000 buckets. Edge buckets are clipped to the requested range. Empty buckets have null quantities.",
                }
            ]
            item_schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["start", "end", "metrics"],
                    "properties": {"start": date, "end": date, "metrics": metrics},
                },
            }
        if view == "breakdown":
            props["group_by"] = {"type": "string", "enum": ["realm", "resource"]}
            params += [
                {
                    "name": "group_by",
                    "in": "query",
                    "schema": {**props["group_by"], "default": "realm"},
                },
                {
                    "name": "page_limit",
                    "in": "query",
                    "schema": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                    "description": "Number of groups, each with all selected metrics.",
                },
                {
                    "name": "page_marker",
                    "in": "query",
                    "schema": uuid,
                    "description": "Last group UUID from X-Pagination-Marker; keep filters and group_by unchanged between pages.",
                },
            ]
            item_schema = {
                "type": "array",
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "required": [name, "metrics"],
                            "properties": {name: uuid, "metrics": metrics},
                        }
                        for name in ("realm_uuid", "resource_uuid")
                    ]
                },
            }
        props["items"] = item_schema
        name = "Usage" + title
        schemas[name] = {"type": "object", "required": list(props), "properties": props}
        operation = spec["paths"][f"/v1/usage/{view}/"]["get"]
        operation.update(
            {
                "summary": f"Read usage {view}",
                "operationId": f"Get_usage_{view}",
                "description": "Requires usage.interval.read. Quantities integrate allocated capacity or actual CPU and memory usage over time; these are not instantaneous utilization percentages. UTC range up to 31 days, on five-minute boundaries. Missing data is never zero-filled. Complete coverage refers only to observed ownership; attribution before the first observation is unknown. Stored windows may be repaired later.",
                "parameters": params,
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "Resource usage and measurement coverage.",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{name}"}
                            }
                        },
                    },
                    "400": {
                        "description": "Invalid range, filter, pagination or more than 2000 time buckets."
                    },
                    "401": {"description": "Missing, invalid or expired token."},
                    "403": {
                        "description": "Permission denied or project outside token scope."
                    },
                },
            }
        )
        if view == "breakdown":
            operation["responses"]["200"]["headers"] = {
                "X-Pagination-Marker": {
                    "schema": uuid,
                    "description": "Present only when another page of groups is available.",
                }
            }


def add_current_spec(spec):
    uuid = {"type": "string", "format": "uuid"}
    percent = {"type": "number", "minimum": 0, "maximum": 100}

    def measurement(properties):
        return {
            "type": "object",
            "nullable": True,
            "required": list(properties),
            "properties": properties,
        }

    properties = {
        "project_id": uuid,
        "realm_uuid": uuid,
        "resource_uuid": uuid,
        "observed_at": {
            "type": "string",
            "format": "date-time",
            "description": "Evaluation time of the current snapshot.",
        },
        "status": {"type": "string", "enum": ["available", "partial", "no_data"]},
        "cpu": measurement(
            {
                "total_cores": {"type": "integer", "minimum": 1},
                "used_cores": {"type": "number", "minimum": 0},
                "utilization_percent": percent,
            }
        ),
        "memory": measurement(
            {
                "total_bytes": {"type": "integer", "minimum": 1},
                "used_bytes": {"type": "integer", "minimum": 0},
                "utilization_percent": percent,
            }
        ),
        "disk": measurement(
            {
                "mountpoint": {"type": "string", "enum": ["/"]},
                "total_bytes": {"type": "integer", "minimum": 1},
                "used_bytes": {"type": "integer", "minimum": 0},
                "available_bytes": {"type": "integer", "minimum": 0},
                "utilization_percent": percent,
            }
        ),
    }
    spec["components"]["schemas"]["UsageCurrent"] = {
        "type": "object",
        "required": list(properties),
        "properties": properties,
    }
    spec["paths"]["/v1/usage/current/"]["get"].update(
        {
            "summary": "Read current realm CPU, memory and root filesystem utilization",
            "operationId": "Get_usage_current",
            "description": "Requires usage.interval.read and an active realm binding in the selected project. CPU is averaged over one minute; memory is MemTotal minus MemAvailable. Disk describes mountpoint /, used bytes are size minus free, and available bytes exclude reserved space. Totals are reported by the guest OS, so memory can be lower than allocated RAM. Samples older than 120 seconds are excluded. Missing metrics are null, never zero-filled. No historical usage intervals are written.",
            "security": [{"bearerAuth": []}],
            "parameters": [
                {"name": "realm_uuid", "in": "query", "required": True, "schema": uuid},
                {
                    "name": "project_id",
                    "in": "query",
                    "schema": uuid,
                    "description": "Required for an unscoped token; inferred from a project token.",
                },
            ],
            "responses": {
                "200": {
                    "description": "Current snapshot, possibly partial or unavailable due to absent samples.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/UsageCurrent"}
                        }
                    },
                },
                "400": {"description": "Invalid realm or project query."},
                "401": {"description": "Missing, invalid or expired token."},
                "403": {
                    "description": "Permission denied or project outside token scope."
                },
                "404": {
                    "description": "No active realm binding in the selected project."
                },
                "503": {
                    "description": "VictoriaMetrics unavailable or exporter identity ambiguous."
                },
            },
        }
    )
