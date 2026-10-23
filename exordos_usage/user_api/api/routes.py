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

from restalchemy.api import routes

from exordos_usage.user_api.api import controllers


class HealthRoute(routes.Route):
    __controller__ = controllers.HealthController
    __allow_methods__ = (routes.FILTER,)


class SummaryRoute(routes.Route):
    __controller__ = controllers.SummaryController
    __allow_methods__ = (routes.FILTER,)


class TimeseriesRoute(routes.Route):
    __controller__ = controllers.TimeseriesController
    __allow_methods__ = (routes.FILTER,)


class BreakdownRoute(routes.Route):
    __controller__ = controllers.BreakdownController
    __allow_methods__ = (routes.FILTER,)


class UsageRoute(routes.Route):
    __controller__ = controllers.UsageRoutesController
    __allow_methods__ = (routes.FILTER,)

    summary = routes.route(SummaryRoute)
    timeseries = routes.route(TimeseriesRoute)
    breakdown = routes.route(BreakdownRoute)


class ApiEndpointRoute(routes.Route):
    __controller__ = controllers.ApiEndpointController
    __allow_methods__ = (routes.FILTER,)

    usage = routes.route(UsageRoute)
