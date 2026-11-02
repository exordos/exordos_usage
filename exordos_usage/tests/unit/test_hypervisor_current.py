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

from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from webob import Request

from exordos_usage.common.clients import Core
from exordos_usage.tests.functional.test_api import authenticated_app

PATH = "/v1/usage/hypervisors/current/"


@pytest.fixture(autouse=True)
def unused_database_session():
    # WSGI middleware opens a session even though this endpoint does not use it.
    with patch("restalchemy.storage.sql.engines.engine_factory.get_engine") as engine:
        yield
        engine.return_value.get_session.return_value.execute.assert_not_called()


def test_host_snapshot_uses_authorized_core_node():
    host, node = uuid4(), uuid4()
    app, token = authenticated_app(uuid4())
    with (
        patch("exordos_usage.user_api.api.controllers.Core.hypervisor") as lookup,
        patch("exordos_usage.user_api.api.controllers.Victoria") as metrics,
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
    ):
        lookup.return_value = (
            200,
            {"driver_spec": {"kind": "exordos_local_hyper", "node": str(node)}},
        )
        metrics.return_value.current.return_value = {
            "status": "no_data",
            "cpu": None,
            "memory": None,
            "disk": None,
        }
        response = Request.blank(
            PATH + f"?hypervisor_uuid={host}",
            headers={"Authorization": "Bearer " + token},
        ).get_response(app)
        assert response.status_int == 200
        assert response.json["hypervisor_uuid"] == str(host)
        assert response.json["resource_uuid"] == str(node)
        lookup.assert_called_once_with(host, "Bearer " + token)
        assert metrics.return_value.current.call_args.args[0] == node


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("invalid_token", 403),
        ("denied", 403),
        ("missing", 404),
        ("unsupported", 422),
        ("outage", 503),
        ("metrics_outage", 503),
        ("bad_node", 503),
    ],
)
def test_host_access_and_upstream_failures(scenario, expected):
    app, token = authenticated_app(uuid4(), "no_permission")
    with (
        patch("exordos_usage.user_api.api.controllers.Core.hypervisor") as lookup,
        patch("exordos_usage.user_api.api.controllers.Victoria") as metrics,
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
    ):
        lookup.return_value = (
            200,
            {"driver_spec": {"kind": "exordos_local_hyper", "node": str(uuid4())}},
        )
        if scenario == "invalid_token":
            lookup.return_value = 401, None
        if scenario in {"denied", "missing"}:
            lookup.return_value = expected, None
        if scenario == "unsupported":
            lookup.return_value = 200, {"driver_spec": {"kind": "libvirt"}}
        if scenario == "bad_node":
            lookup.return_value = (
                200,
                {"driver_spec": {"kind": "exordos_local_hyper", "node": "bad"}},
            )
        if scenario == "outage":
            lookup.side_effect = RuntimeError("private details")
        if scenario == "metrics_outage":
            metrics.return_value.current.side_effect = RuntimeError("private details")
        response = Request.blank(
            PATH + f"?hypervisor_uuid={uuid4()}",
            headers={"Authorization": "Bearer " + token},
        ).get_response(app)
        assert response.status_int == expected
        assert "private details" not in response.text
        if scenario != "metrics_outage":
            metrics.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "",
        "hypervisor_uuid=bad",
        f"hypervisor_uuid={uuid4()}&hypervisor_uuid={uuid4()}",
        f"hypervisor_uuid={uuid4()}&project_id={uuid4()}",
        f"hypervisor_uuid={uuid4()}&realm_uuid={uuid4()}",
    ],
)
def test_invalid_host_query_never_contacts_upstreams(query):
    app, token = authenticated_app(uuid4())
    with patch("exordos_usage.user_api.api.controllers.Core.hypervisor") as lookup:
        response = Request.blank(
            PATH + "?" + query, headers={"Authorization": "Bearer " + token}
        ).get_response(app)
        assert response.status_int == 400
        lookup.assert_not_called()


def test_anonymous_host_query_is_rejected():
    app, _ = authenticated_app(uuid4())
    with patch("exordos_usage.user_api.api.controllers.Core.hypervisor") as lookup:
        response = Request.blank(PATH + f"?hypervisor_uuid={uuid4()}").get_response(app)
        assert response.status_int in (401, 403)
        lookup.assert_not_called()


@pytest.mark.parametrize("status", [200, 403, 404, 302, 401, 500])
def test_core_lookup_uses_caller_token_without_redirects(status):
    host = uuid4()
    response = Mock(status_code=status)
    response.json.return_value = {"uuid": str(host), "driver_spec": {}}
    with (
        patch(
            "exordos_usage.common.clients.requests.get", return_value=response
        ) as get,
        patch("exordos_usage.common.clients.cfg") as config,
    ):
        config.CONF.ecosystem.core_endpoint = "http://core/"
        if status in (200, 401, 403, 404):
            code, body = Core.hypervisor(host, "Bearer caller")
            assert code == status
            assert body == (response.json.return_value if status == 200 else None)
        else:
            with pytest.raises(ValueError):
                Core.hypervisor(host, "Bearer caller")
        get.assert_called_once_with(
            f"http://core/v1/compute/hypervisors/{host}",
            headers={"Authorization": "Bearer caller"},
            timeout=(5, 10),
            allow_redirects=False,
        )


def test_core_lookup_rejects_wrong_identity():
    response = Mock(status_code=200)
    response.json.return_value = {"uuid": str(uuid4())}
    with (
        patch("exordos_usage.common.clients.requests.get", return_value=response),
        patch("exordos_usage.common.clients.cfg") as config,
    ):
        config.CONF.ecosystem.core_endpoint = "http://core"
        with pytest.raises(ValueError, match="different resource"):
            Core.hypervisor(uuid4(), "Bearer caller")


@pytest.mark.parametrize("status", [200, 403, 404, 503])
def test_node_current_uses_node_access_and_shared_measurements(status):
    node = uuid4()
    app, token = authenticated_app(uuid4(), "no_permission")
    with (
        patch("exordos_usage.user_api.api.controllers.Core.node") as lookup,
        patch("exordos_usage.user_api.api.controllers.Victoria") as metrics,
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
    ):
        lookup.return_value = status, {"uuid": str(node)}
        if status == 503:
            lookup.side_effect = RuntimeError("private details")
        metrics.return_value.current.return_value = {
            "status": "no_data",
            "cpu": None,
            "memory": None,
            "disk": None,
        }
        response = Request.blank(
            f"/v1/usage/nodes/current/?node_uuid={node}",
            headers={"Authorization": "Bearer " + token},
        ).get_response(app)
        assert response.status_int == status
        lookup.assert_called_once_with(node, "Bearer " + token)
        if status == 200:
            assert (
                response.json["node_uuid"]
                == response.json["resource_uuid"]
                == str(node)
            )
            assert "hypervisor_uuid" not in response.json
            assert metrics.return_value.current.call_args.args[0] == node
        else:
            metrics.assert_not_called()


def test_core_node_lookup_uses_node_collection():
    node = uuid4()
    response = Mock(status_code=200)
    response.json.return_value = {"uuid": str(node)}
    with (
        patch(
            "exordos_usage.common.clients.requests.get", return_value=response
        ) as get,
        patch("exordos_usage.common.clients.cfg") as config,
    ):
        config.CONF.ecosystem.core_endpoint = "http://core/"
        assert Core.node(node, "Bearer caller")[0] == 200
        assert get.call_args.args[0] == f"http://core/v1/compute/nodes/{node}"


@pytest.mark.parametrize(
    "collection,key", [("nodes", "node_uuid"), ("hypervisors", "hypervisor_uuid")]
)
def test_current_lookup_obeys_restalchemy_resource_routing(collection, key):
    from restalchemy.api import applications, controllers, routes
    from restalchemy.api.middlewares.errors import ErrorsHandlerMiddleware

    resource, node = uuid4(), uuid4()
    app, token = authenticated_app(uuid4(), "no_permission")

    class ComputeResourceController(controllers.Controller):
        def get(self, uuid):
            assert self.request.headers["Authorization"] == "Bearer " + token
            assert uuid == str(resource)
            return {
                "uuid": uuid,
                "driver_spec": {"kind": "exordos_local_hyper", "node": str(node)},
            }

    class ResourceRoute(routes.Route):
        __controller__ = ComputeResourceController
        __allow_methods__ = (routes.GET,)

    class ComputeRoute(routes.Route):
        __controller__ = controllers.RoutesListController

        nodes = routes.route(ResourceRoute)
        hypervisors = routes.route(ResourceRoute)

    class VersionRoute(routes.Route):
        __controller__ = controllers.RoutesListController

        compute = routes.route(ComputeRoute)

    class RootRoute(routes.RootRoute):
        v1 = routes.route(VersionRoute)

    core = ErrorsHandlerMiddleware(applications.WSGIApp(RootRoute))

    def core_get(url, headers, **kwargs):
        response = Request.blank(url, headers=headers).get_response(core)
        return Mock(
            status_code=response.status_int, json=Mock(return_value=response.json)
        )

    # The framework distinguishes resource URLs from collection URLs.
    assert (
        core_get(
            f"http://core/v1/compute/{collection}/{resource}/",
            {"Authorization": "Bearer " + token},
        ).status_code
        == 404
    )
    with (
        patch("exordos_usage.common.clients.requests.get", side_effect=core_get),
        patch("exordos_usage.common.clients.cfg") as config,
        patch("exordos_usage.user_api.api.controllers.Victoria") as metrics,
        patch("exordos_usage.user_api.api.controllers.cfg", Mock()),
    ):
        config.CONF.ecosystem.core_endpoint = "http://core"
        metrics.return_value.current.return_value = {"status": "no_data"}
        response = Request.blank(
            f"/v1/usage/{collection}/current/?{key}={resource}",
            headers={"Authorization": "Bearer " + token},
        ).get_response(app)
        assert response.status_int == 200
        assert response.json[key] == str(resource)
        assert metrics.return_value.current.call_args.args[0] == (
            resource if collection == "nodes" else node
        )
