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
from restalchemy.common import contexts
from webob import Request

from exordos_usage.tests.functional.test_api import authenticated_app
from exordos_usage.user_api.dm.models import UsageBinding


def binding(project, realm, closed=False):
    with contexts.Context().session_manager() as conn:
        UsageBinding(
            resource_uuid=uuid4(),
            realm_uuid=realm,
            project_id=project,
            valid_from=datetime(2026, 10, 1, tzinfo=timezone.utc),
            valid_until=datetime(2026, 10, 2, tzinfo=timezone.utc) if closed else None,
        ).insert(session=conn)


def request(app, token, realm, extra=""):
    return Request.blank(
        f"/v1/usage/current/?realm_uuid={realm}{extra}",
        headers={"Authorization": "Bearer " + token},
    ).get_response(app)


def test_current_scopes_binding_and_returns_live_data(dsn):
    project, realm = uuid4(), uuid4()
    binding(project, realm)
    app, token = authenticated_app(project)
    result = {
        "observed_at": datetime.now(timezone.utc),
        "status": "available",
        "cpu": {"total_cores": 4, "used_cores": 1.68, "utilization_percent": 42},
        "memory": {"total_bytes": 100, "used_bytes": 70, "utilization_percent": 70},
    }
    with (
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
        patch("exordos_usage.user_api.api.controllers.Victoria") as victoria,
    ):
        victoria.return_value.current.return_value = result
        response = request(app, token, realm)
        assert response.status_int == 200
        assert response.json["project_id"] == str(project)
        assert response.json["realm_uuid"] == str(realm)
        assert response.json["cpu"]["utilization_percent"] == 42
        assert (
            str(victoria.return_value.current.call_args.args[0])
            == response.json["resource_uuid"]
        )


@pytest.mark.parametrize(
    "scenario,expected",
    [("other_project", 404), ("closed", 404), ("unknown", 404), ("no_permission", 403)],
)
def test_unauthorized_or_inactive_binding_never_queries_metrics(
    dsn, scenario, expected
):
    project, realm = uuid4(), uuid4()
    if scenario != "unknown":
        binding(
            uuid4() if scenario == "other_project" else project,
            realm,
            scenario == "closed",
        )
    app, token = authenticated_app(project, scenario)
    with patch("exordos_usage.user_api.api.controllers.Victoria") as victoria:
        assert request(app, token, realm).status_int == expected
        victoria.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "",
        "realm_uuid=bad",
        f"realm_uuid={uuid4()}&extra=1",
        f"realm_uuid={uuid4()}&realm_uuid={uuid4()}",
    ],
)
def test_invalid_current_query(dsn, query):
    app, token = authenticated_app(uuid4())
    response = Request.blank(
        "/v1/usage/current/?" + query, headers={"Authorization": "Bearer " + token}
    ).get_response(app)
    assert response.status_int == 400


def test_victoria_failure_returns_503_without_private_details(dsn):
    project, realm = uuid4(), uuid4()
    binding(project, realm)
    app, token = authenticated_app(project)
    with (
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
        patch("exordos_usage.user_api.api.controllers.Victoria") as victoria,
    ):
        victoria.return_value.current.side_effect = RuntimeError(
            "private upstream details"
        )
        response = request(app, token, realm)
        assert response.status_int == 503
        assert "private upstream details" not in response.text
