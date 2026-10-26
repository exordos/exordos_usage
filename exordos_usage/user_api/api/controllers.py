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

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from gcl_iam import exceptions as iam_exceptions
from gcl_iam import rules
from oslo_config import cfg
from restalchemy.api import controllers
from restalchemy.common import contexts, exceptions
from restalchemy.dm import filters as sql_filters

from exordos_usage.common.clients import Ecosystem, Victoria
from exordos_usage.user_api.dm import reports
from exordos_usage.user_api.dm.models import UsageBinding
from exordos_usage.user_api.dm.usage import milliseconds, utc


class InvalidUsageQuery(exceptions.ValidationErrorException):
    message = "Select a project and a valid UTC range up to 31 days."


def select_project(iam, requested):
    iam.enforcer.enforce(rules.Rule("usage", "interval", "read"), do_raise=True)
    scope = iam.introspection_info().get("project_id")
    if scope and requested and UUID(str(scope)) != UUID(requested):
        raise iam_exceptions.Forbidden()
    if not scope and not requested:
        raise InvalidUsageQuery()
    return UUID(str(scope or requested))


class HealthController(controllers.Controller):
    def filter(self, filters, order_by=None):
        """Check service health without authentication."""
        return {"status": "ok"}


class ApiEndpointController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/"


class UsageRoutesController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/usage/"


class ReportController(controllers.Controller):
    view = "summary"

    def filter(self, filters, order_by=None):
        """Read usage totals, a time series or a grouped breakdown."""
        params = self.request.GET
        allowed = {
            "start",
            "end",
            "project_id",
            "realm_uuid",
            "resource_uuid",
            "metric",
        }
        if self.view == "timeseries":
            allowed.add("step")
        if self.view == "breakdown":
            allowed.update({"group_by", "page_limit", "page_marker"})
        if set(params) - allowed or any(len(params.getall(k)) != 1 for k in params):
            raise InvalidUsageQuery()
        context = contexts.get_context()
        try:
            project = select_project(context.iam_context, params.get("project_id"))
            start, end = utc(params["start"]), utc(params["end"])
            if end <= start or end - start > timedelta(days=31):
                raise InvalidUsageQuery()
            if any(
                value.minute % 5 or value.second or value.microsecond
                for value in (start, end)
            ):
                raise InvalidUsageQuery()
            realm = UUID(params["realm_uuid"]) if "realm_uuid" in params else None
            resource = (
                UUID(params["resource_uuid"]) if "resource_uuid" in params else None
            )
            metric = params.get("metric")
            if metric is not None and metric not in reports.METRIC_UNITS:
                raise InvalidUsageQuery()
            step = params.get("step", "1h")
            if step not in reports.STEPS:
                raise InvalidUsageQuery()
            if self.view == "timeseries":
                seconds = reports.STEPS[step]
                points = (
                    (milliseconds(end) - 1) // (seconds * 1000)
                    - milliseconds(start) // (seconds * 1000)
                    + 1
                )
                if points > 2000:
                    raise InvalidUsageQuery()
            group_by = params.get("group_by", "realm")
            if group_by not in {"realm", "resource"}:
                raise InvalidUsageQuery()
            limit = int(params.get("page_limit", "100"))
            if not 1 <= limit <= 200:
                raise InvalidUsageQuery()
            marker = UUID(params["page_marker"]) if "page_marker" in params else None
        except (ValueError, KeyError, OverflowError) as exc:
            raise InvalidUsageQuery() from exc
        result, next_marker = reports.read_report(
            context.get_session(),
            project,
            start,
            end,
            view=self.view,
            realm=realm,
            resource=resource,
            metric=metric,
            step=step,
            group_by=group_by,
            page_limit=limit,
            marker=marker,
        )
        response = {
            "project_id": str(project),
            "start": start,
            "end": end,
            **({"step": step} if self.view == "timeseries" else {}),
            **({"group_by": group_by} if self.view == "breakdown" else {}),
            **result,
        }
        headers = {"X-Pagination-Marker": next_marker} if next_marker else {}
        return response, 200, headers


class SummaryController(ReportController):
    view = "summary"


class TimeseriesController(ReportController):
    view = "timeseries"


class BreakdownController(ReportController):
    view = "breakdown"


class CurrentUsageUnavailable(exceptions.RestAlchemyException):
    code = 503
    message = "Current resource usage is temporarily unavailable."


class InvalidCurrentUsageQuery(exceptions.ValidationErrorException):
    message = "Select a project and a valid realm_uuid."


class CurrentUsageController(controllers.Controller):
    def filter(self, filters, order_by=None):
        """Read current CPU and memory utilization for a project realm."""
        params = self.request.GET
        if set(params) - {"project_id", "realm_uuid"} or any(
            len(params.getall(key)) != 1 for key in params
        ):
            raise InvalidCurrentUsageQuery()
        context = contexts.get_context()
        try:
            project = select_project(context.iam_context, params.get("project_id"))
            realm = UUID(params["realm_uuid"])
        except (ValueError, KeyError) as exc:
            raise InvalidCurrentUsageQuery() from exc
        # Resolve ownership before making any external metrics request.
        bindings = UsageBinding.objects.get_all(
            session=context.get_session(),
            filters={
                "project_id": sql_filters.EQ(project),
                "realm_uuid": sql_filters.EQ(realm),
                "valid_until": sql_filters.Is(None),
            },
        )
        if not bindings:
            raise exceptions.NotFoundError(path=self.request.path)
        if len(bindings) != 1:
            raise CurrentUsageUnavailable()
        binding = bindings[0]
        try:
            ownership = Ecosystem.from_config().realm(realm)
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Failed to verify current realm ownership"
            )
            raise CurrentUsageUnavailable() from exc
        if (
            ownership is None
            or ownership["project_id"] != project
            or ownership["resource_uuid"] != binding.resource_uuid
        ):
            raise exceptions.NotFoundError(path=self.request.path)
        settings = cfg.CONF.victoria
        try:
            result = Victoria(
                settings.endpoint, settings.username, settings.password
            ).current(binding.resource_uuid, datetime.now(timezone.utc))
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Failed to read current resource usage"
            )
            raise CurrentUsageUnavailable() from exc
        return {
            "project_id": str(project),
            "realm_uuid": str(realm),
            "resource_uuid": str(binding.resource_uuid),
            **result,
        }
