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

from gcl_iam import middlewares as iam_mw
from restalchemy.api import applications, routes
from restalchemy.openapi import structures

from exordos_usage import version
from exordos_usage.common.openapi import UsageOpenApiEngine
from exordos_usage.user_api.api import routes as app_routes
from exordos_usage.user_api.api import versions


class UserApiApp(routes.RootRoute):
    health = routes.route(app_routes.HealthRoute)


setattr(UserApiApp, versions.API_VERSION_1_0, routes.route(app_routes.ApiEndpointRoute))


def get_api_application():
    return UserApiApp


def get_openapi_engine():
    return UsageOpenApiEngine(
        info=structures.OpenApiInfo(
            title="Exordos Usage v1 User API",
            version=version.version_info,
            description="Resource usage history. Requires usage.interval.read permission.",
        ),
        paths=structures.OpenApiPaths(),
    )


def build_wsgi_application(iam_engine_driver):
    application = applications.OpenApiApplication(
        route_class=get_api_application(), openapi_engine=get_openapi_engine()
    )
    authenticated = iam_mw.GenesisCoreAuthMiddleware(
        application,
        iam_engine_driver,
        skip_auth_endpoints=[iam_mw.EndpointComparator("/health/?")],
    )
    return iam_mw.ErrorsHandlerMiddleware(authenticated)
