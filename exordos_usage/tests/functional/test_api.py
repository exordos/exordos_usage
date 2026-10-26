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


import time
from unittest.mock import Mock, patch
from uuid import uuid4

import jwt
from gcl_iam import drivers
from webob import Request

from exordos_usage.user_api.api.app import build_wsgi_application


def test_wsgi_rejects_anonymous_usage_and_serves_health(dsn):
    app = build_wsgi_application(Mock())
    assert Request.blank("/health/").get_response(app).status_int == 200
    with patch("exordos_usage.user_api.api.controllers.reports.read_report") as read:
        assert Request.blank("/v1/usage/summary/?start=bad").get_response(
            app
        ).status_int in (
            401,
            403,
        )
        read.assert_not_called()


def test_usage_catalog_lists_report_endpoints(dsn):
    app, token = authenticated_app(uuid4())
    response = Request.blank(
        "/v1/usage/", headers={"Authorization": "Bearer " + token}
    ).get_response(app)
    assert response.status_int == 200
    assert set(response.json) == {"summary", "timeseries", "breakdown", "current"}


def authenticated_app(project, scenario="own"):
    driver = drivers.DummyDriver()
    driver.project_id = str(project)
    driver.permissions = [] if scenario == "no_permission" else ["usage.interval.read"]
    key = "test-signing-key-for-usage-api-32-bytes"
    driver.algorithm_keys["usage-test"] = drivers.HS256AlgorithmKeys(key=key)
    token = jwt.encode(
        {
            "aud": "usage-test",
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int(time.time()) - 120,
            "exp": int(time.time()) + (-60 if scenario == "expired" else 60),
        },
        key,
        algorithm="HS256",
    )
    return build_wsgi_application(driver), token
