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

from datetime import timedelta
from uuid import UUID

from gcl_iam import exceptions as iam_exceptions
from gcl_iam import rules
from restalchemy.api import controllers
from restalchemy.common import contexts, exceptions

from exordos_usage.user_api.dm import reports
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
