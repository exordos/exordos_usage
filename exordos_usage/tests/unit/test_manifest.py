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

from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).parents[3]


def manifest():
    template = Environment(undefined=StrictUndefined).from_string(
        (ROOT / "exordos/manifests/usage.yaml.j2").read_text()
    )
    return yaml.safe_load(
        template.render(
            version="0.1.0",
            images={"usage": "urn:images:11111111-1111-4111-8111-111111111111"},
        )
    )


def test_collector_has_only_existing_realm_read_permission():
    doc = manifest()
    bindings = doc["resources"]["$core.iam.permissionbinding"]
    collector = [
        b
        for b in bindings.values()
        if b["role"] == "$core.iam.roles.$usage_collector:uuid"
    ]
    assert collector == [
        {
            "role": "$core.iam.roles.$usage_collector:uuid",
            "permission": "$usage.imports.$realm_read:uuid",
            "project_id": None,
        }
    ]
    assert (
        doc["imports"]["realm_read"]["link"] == "$core.iam.permissions.$eco_realm_read"
    )
    assert (
        doc["resources"]["$core.iam.rolebinding"]["usage_collector_global"]["project"]
        is None
    )


def test_secret_config_and_database_live_outside_root_image():
    doc = manifest()
    config = doc["resources"]["$core.config.configs"]["usage_config"]
    assert config["mode"] == "0600"
    assert "$core.vs.variables" not in doc["resources"]
    assert config["body"]["kind"] == "text"
    assert config["body"]["content"].startswith('f"')
    assert "chmod 600" in config["on_change"]["command"]
    assert "postgresql://usage_db_user:" in config["body"]["content"]
    assert doc["imports"]["pg"]["element"] == "$communal_pg_cluster"


def test_build_references_existing_image_script():
    build = yaml.safe_load((ROOT / "exordos/exordos.yaml").read_text())["build"]
    for element in build["elements"]:
        assert (ROOT / "exordos" / element["manifest"]).is_file()
        for image in element["images"]:
            assert (ROOT / "exordos" / image["script"]).is_file()


def test_ecosystem_service_has_read_only_usage_role():
    doc = manifest()
    assert doc["imports"]["ecosystem_service_user"] == {
        "element": "$exordos_ecosystem",
        "kind": "resource",
        "link": "$core.iam.users.$ecosystem_admin_user",
    }
    assert doc["resources"]["$core.iam.rolebinding"]["ecosystem_usage_viewer"] == {
        "role": "$core.iam.roles.$usage_viewer:uuid",
        "user": "$usage.imports.$ecosystem_service_user:uuid",
        "project": None,
    }
    permissions = [
        binding
        for binding in doc["resources"]["$core.iam.permissionbinding"].values()
        if binding["role"] == "$core.iam.roles.$usage_viewer:uuid"
    ]
    assert permissions == [
        {
            "role": "$core.iam.roles.$usage_viewer:uuid",
            "permission": "$core.iam.permissions.$usage_interval_read:uuid",
            "project_id": None,
        }
    ]
