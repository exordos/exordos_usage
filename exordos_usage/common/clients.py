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

"""Read-only adapters for ecosystem attribution and VictoriaMetrics export."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import requests

from exordos_usage.user_api.dm.usage import METRICS


class Ecosystem:
    def __init__(self, endpoint: str, auth):
        self.endpoint = endpoint.rstrip("/")
        self.auth = auth

    def _page(self, params):
        response = requests.get(
            self.endpoint + "/v1/realms/",
            params=dict(params),
            headers=self.auth.get_auth_header(),
            timeout=30,
        )
        if response.status_code == 401:
            self.auth.authenticate()
            response = requests.get(
                self.endpoint + "/v1/realms/",
                params=dict(params),
                headers=self.auth.get_auth_header(),
                timeout=30,
            )
        response.raise_for_status()
        return response

    def bindings(self) -> tuple:
        bindings, seen, markers = [], set(), set()
        params = {"kind": "MANAGED", "page_limit": 200}
        while True:
            response = self._page(params)
            body = response.json()
            if not isinstance(body, list):
                raise ValueError("Expected a realm collection")
            for item in body:
                if item["kind"] != "MANAGED":
                    raise ValueError("Unexpected realm kind")
                if item["node_uuid"] is None:
                    continue
                binding = {
                    "realm_uuid": str(UUID(item["uuid"])),
                    "resource_uuid": str(UUID(item["node_uuid"])),
                    "project_id": str(UUID(item["project_id"]))
                    if item["project_id"]
                    else None,
                    "source_realm_uuid": str(UUID(item["host_realm"]))
                    if item["host_realm"]
                    else None,
                }
                if binding["resource_uuid"] in seen:
                    raise ValueError("Ambiguous resource ownership")
                seen.add(binding["resource_uuid"])
                bindings.append(binding)
            marker = response.headers.get("X-Pagination-Marker")
            if not marker:
                break
            marker = str(UUID(marker))
            if marker in markers:
                raise ValueError("Repeated pagination marker")
            markers.add(marker)
            params["page_marker"] = marker
        return datetime.now(timezone.utc), bindings


class Victoria:
    def __init__(
        self, endpoint: str, username: str, password: str, exporter_instance: str
    ):
        if not exporter_instance:
            raise ValueError("An explicit Core exporter instance is required")
        self.endpoint = endpoint.rstrip("/")
        self.auth = (username, password)
        # The selector isolates this Core from other producers in shared storage.
        self.selector = (
            '{__name__=~"'
            + "|".join(METRICS)
            + '",instance='
            + json.dumps(exporter_instance)
            + "}"
        )

    def samples(self, start_ms: int, end_ms: int) -> dict:
        result = {}
        with requests.get(
            self.endpoint + "/api/v1/export",
            params={
                "match[]": self.selector,
                "start": str(Decimal(start_ms) / 1000),
                "end": str(Decimal(end_ms) / 1000),
            },
            auth=self.auth,
            timeout=(10, 60),
            stream=True,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                row = json.loads(line, parse_float=Decimal)
                labels = row["metric"]
                metric = labels["__name__"]
                if metric not in METRICS:
                    raise ValueError("Unexpected metric in export")
                node = str(UUID(labels["node_uuid"]))
                values, timestamps = row["values"], row["timestamps"]
                if len(values) != len(timestamps):
                    raise ValueError("Invalid export sample arrays")
                result.setdefault((node, metric), []).extend(
                    (int(t), Decimal(str(v))) for t, v in zip(timestamps, values)
                )
        return result
