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
import math
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
                    if item.get("host_realm")
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
    def __init__(self, endpoint: str, username: str, password: str):
        self.endpoint = endpoint.rstrip("/")
        self.auth = (username, password)

    def samples(self, start_ms: int, end_ms: int, resource_uuids) -> dict:
        nodes = {str(UUID(str(node))) for node in resource_uuids}
        if not nodes:
            return {}
        selector = (
            '{__name__=~"'
            + "|".join(METRICS)
            + '",node_uuid=~'
            + json.dumps("|".join(sorted(nodes)))
            + "}"
        )
        result = {}
        with requests.get(
            self.endpoint + "/api/v1/export",
            params={
                "match[]": selector,
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
                if node not in nodes:
                    raise ValueError("Unexpected node in export")
                values, timestamps = row["values"], row["timestamps"]
                if len(values) != len(timestamps):
                    raise ValueError("Invalid export sample arrays")
                result.setdefault((node, metric), []).extend(
                    (int(t), Decimal(str(v))) for t, v in zip(timestamps, values)
                )
        return result

    def query(self, expression: str, now: datetime) -> list:
        response = requests.get(
            self.endpoint + "/api/v1/query",
            params={"query": expression, "time": now.timestamp()},
            auth=self.auth,
            timeout=(5, 10),
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success" or body["data"]["resultType"] != "vector":
            raise ValueError("Invalid VictoriaMetrics query response")
        return body["data"]["result"]

    def current(self, resource_uuid, now: datetime) -> dict:
        # Instant queries may return old samples within the lookback window.
        def fresh(selector):
            return f"({selector}) and (time() - timestamp({selector}) <= 120)"

        info = self.query(
            fresh(
                "exordos_compute_node_info{node_uuid="
                + json.dumps(str(resource_uuid))
                + "}"
            ),
            now,
        )
        result = {
            "observed_at": now,
            "status": "no_data",
            "cpu": None,
            "memory": None,
            "disk": None,
        }
        if not info:
            return result
        if len(info) != 1 or not info[0]["metric"].get("hostname"):
            raise ValueError("Ambiguous node exporter identity")
        selector = 'job="node_exporter",instance=' + json.dumps(
            info[0]["metric"]["hostname"]
        )
        idle = "node_cpu_seconds_total{" + selector + ',mode="idle"}'
        rates = self.query(
            f"rate({idle}[1m]) and (time() - timestamp({idle}) <= 120)", now
        )
        if rates:
            cpus = [row["metric"]["cpu"] for row in rates]
            values = [float(row["value"][1]) for row in rates]
            if len(set(cpus)) != len(cpus):
                raise ValueError("Ambiguous CPU samples")
            if all(math.isfinite(value) for value in values):
                used = sum(1 - min(1, max(0, value)) for value in values)
                result["cpu"] = {
                    "total_cores": len(values),
                    "used_cores": used,
                    "utilization_percent": used / len(values) * 100,
                }
        memory = []
        for metric in ("node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes"):
            rows = self.query(fresh(metric + "{" + selector + "}"), now)
            if len(rows) > 1:
                raise ValueError("Ambiguous memory samples")
            memory.append(float(rows[0]["value"][1]) if rows else None)
        total, available = memory
        if (
            total is not None
            and available is not None
            and math.isfinite(total)
            and math.isfinite(available)
            and total > 0
            and 0 <= available <= total
        ):
            used = int(total - available)
            result["memory"] = {
                "total_bytes": int(total),
                "used_bytes": used,
                "utilization_percent": used / total * 100,
            }
        filesystem = (
            '{__name__=~"node_filesystem_(size|free|avail)_bytes",'
            + selector
            + ',mountpoint="/",fstype!~"tmpfs|devtmpfs|overlay|squashfs|proc|sysfs"}'
        )
        rows = self.query(fresh(filesystem), now)
        disk = {}
        identities = set()
        for row in rows:
            labels = row["metric"]
            metric = labels["__name__"]
            if (
                metric
                not in {
                    "node_filesystem_size_bytes",
                    "node_filesystem_free_bytes",
                    "node_filesystem_avail_bytes",
                }
                or metric in disk
            ):
                raise ValueError("Ambiguous filesystem samples")
            identities.add((labels["device"], labels["fstype"]))
            disk[metric] = float(row["value"][1])
        if len(identities) > 1:
            raise ValueError("Mismatched filesystem samples")
        if len(disk) == 3 and all(math.isfinite(value) for value in disk.values()):
            total = disk["node_filesystem_size_bytes"]
            free = disk["node_filesystem_free_bytes"]
            available = disk["node_filesystem_avail_bytes"]
            if total > 0 and 0 <= available <= free <= total:
                used = int(total - free)
                result["disk"] = {
                    "mountpoint": "/",
                    "total_bytes": int(total),
                    "used_bytes": used,
                    "available_bytes": int(available),
                    "utilization_percent": used / total * 100,
                }
        count = sum(result[key] is not None for key in ("cpu", "memory", "disk"))
        result["status"] = (
            "available" if count == 3 else "partial" if count else "no_data"
        )
        return result
