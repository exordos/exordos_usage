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


"""Resource attribution history and atomically checkpointed usage intervals."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from restalchemy.dm import filters, models, properties, relationships, types
from restalchemy.storage.sql import orm

from exordos_usage.user_api.dm.usage import (
    CALCULATION_VERSION,
    METRICS,
    UsageUnit,
    integrate,
    milliseconds,
)


class CollectorState(models.Model, orm.SQLStorableMixin):
    __tablename__ = "usage_collector_state"

    id = properties.property(
        types.Integer(min_value=1, max_value=1), id_property=True, default=1
    )

    next_window = properties.property(types.UTCDateTimeZ(), required=True)
    observed_at = properties.property(
        types.AllowNone(types.UTCDateTimeZ()), default=None
    )


class UsageBinding(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "usage_bindings"

    resource_uuid = properties.property(types.UUID(), required=True)
    realm_uuid = properties.property(types.UUID(), required=True)
    project_id = properties.property(types.AllowNone(types.UUID()), default=None)
    valid_from = properties.property(types.UTCDateTimeZ(), required=True)
    valid_until = properties.property(
        types.AllowNone(types.UTCDateTimeZ()), default=None
    )


class IntervalBindingRelationship(relationships.Relationship):
    @classmethod
    def is_id_property(cls):
        return True


class UsageInterval(models.Model, orm.SQLStorableMixin):
    __tablename__ = "usage_intervals"

    metric = properties.property(
        types.Enum([metric for metric, _ in METRICS.values()]),
        id_property=True,
        required=True,
    )
    period_start = properties.property(
        types.UTCDateTimeZ(), id_property=True, required=True
    )
    period_end = properties.property(
        types.UTCDateTimeZ(), id_property=True, required=True
    )
    binding = relationships.relationship(
        UsageBinding,
        property_class=IntervalBindingRelationship,
        required=True,
        read_only=True,
    )
    window_start = properties.property(types.UTCDateTimeZ(), required=True)
    unit = properties.property(
        types.Enum([unit.value for unit in UsageUnit]), required=True
    )
    quantity = properties.property(types.Decimal(min_value=0), required=True)
    covered_ms = properties.property(types.Integer(min_value=0), required=True)
    expected_ms = properties.property(types.Integer(min_value=1), required=True)
    complete = properties.property(types.Boolean(), required=True)
    calculation_version = properties.property(types.Integer(min_value=1), required=True)


class Store:
    def __init__(self, conn, window_seconds: int):
        self.conn = conn
        self.window = timedelta(seconds=window_seconds)

    def lock(self) -> bool:
        return self.conn.execute(
            "SELECT pg_try_advisory_xact_lock("
            "hashtextextended('exordos_usage.collector', 0)) AS locked"
        ).fetchone()["locked"]

    def _state(self):
        return CollectorState.objects.get_one(
            session=self.conn, filters={"id": filters.EQ(1)}, locked=True
        )

    def observe(self, observed, bindings: list[dict], max_observation_gap=120) -> None:
        """Record new observations without inventing pre-observation ownership."""
        state = CollectorState.objects.get_one_or_none(
            session=self.conn, filters={"id": filters.EQ(1)}, locked=True
        )
        if state is None:
            state = CollectorState(
                next_window=datetime.fromtimestamp(
                    milliseconds(observed)
                    // 1000
                    // int(self.window.total_seconds())
                    * int(self.window.total_seconds()),
                    tz=timezone.utc,
                ),
            )
            state.insert(session=self.conn)
        last = state.observed_at
        if last is not None and observed <= last:
            raise ValueError("Non-increasing observation time")
        existing = {
            str(binding.resource_uuid): binding
            for binding in UsageBinding.objects.get_all(
                session=self.conn,
                filters={
                    "valid_until": filters.Is(None),
                },
            )
        }
        if last and (observed - last).total_seconds() > max_observation_gap:
            # A collector outage is not evidence of unchanged ownership.
            for previous in existing.values():
                previous.valid_until = last
                previous.update(session=self.conn)
            existing = {}
        for binding in bindings:
            node = binding["resource_uuid"]
            previous = existing.pop(node, None)
            if previous and all(
                (
                    str(getattr(previous, key))
                    if getattr(previous, key) is not None
                    else None
                )
                == binding[key]
                for key in ("realm_uuid", "project_id")
            ):
                continue
            if previous:
                previous.valid_until = observed
                previous.update(session=self.conn)
            UsageBinding(
                resource_uuid=UUID(node),
                realm_uuid=UUID(binding["realm_uuid"]),
                project_id=UUID(binding["project_id"])
                if binding["project_id"]
                else None,
                valid_from=observed,
            ).insert(session=self.conn)
        for previous in existing.values():
            previous.valid_until = observed
            previous.update(session=self.conn)
        state.observed_at = observed
        state.update(session=self.conn)

    def next_window(self):
        return self._state().next_window

    def bindings_for_window(self, start, end):
        return UsageBinding.objects.get_all(
            session=self.conn,
            filters=filters.AND(
                {"valid_from": filters.LT(end)},
                filters.OR(
                    {"valid_until": filters.Is(None)},
                    {"valid_until": filters.GT(start)},
                ),
            ),
        )

    def save_window(self, start, end, samples: dict, max_gap_ms: int) -> None:
        """Replace a replayed window and advance the cursor in the same transaction."""
        bindings = self.bindings_for_window(start, end)
        intervals = UsageInterval.objects.get_all(
            session=self.conn, filters={"window_start": filters.EQ(start)}
        )
        self.conn.batch_delete(intervals)
        for binding in bindings:
            left = max(start, binding.valid_from)
            right = min(end, binding.valid_until or end)
            # PostgreSQL timestamps retain microseconds; sampling works in ms.
            if milliseconds(right) - milliseconds(left) <= 0:
                continue
            for raw, (metric, unit) in METRICS.items():
                result = integrate(
                    samples.get((str(binding.resource_uuid), raw), []),
                    milliseconds(left),
                    milliseconds(right),
                    max_gap_ms,
                )
                UsageInterval(
                    binding=binding,
                    window_start=start,
                    period_start=left,
                    period_end=right,
                    metric=metric,
                    unit=unit,
                    quantity=result.quantity,
                    covered_ms=result.covered_ms,
                    expected_ms=result.expected_ms,
                    complete=result.complete,
                    calculation_version=CALCULATION_VERSION,
                ).insert(session=self.conn)
        state = self._state()
        state.next_window = max(state.next_window, end)
        state.update(session=self.conn)
