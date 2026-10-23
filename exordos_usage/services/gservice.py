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

from gcl_looper.services import basic
from gcl_sdk.clients.http.base import CoreIamAuthenticator
from restalchemy.common import contexts

from exordos_usage.common.clients import Ecosystem, Victoria
from exordos_usage.user_api.dm.models import Store
from exordos_usage.user_api.dm.usage import milliseconds

LOG = logging.getLogger(__name__)


class Worker(basic.BasicService):
    def __init__(self, conf):
        super().__init__(iter_min_period=conf.worker.poll_seconds)
        self.conf = conf
        self.host_realm = (
            str(UUID(conf.ecosystem.host_realm)) if conf.ecosystem.host_realm else None
        )
        self.ecosystem = Ecosystem(
            conf.ecosystem.endpoint,
            CoreIamAuthenticator(
                base_url=conf.ecosystem.core_endpoint.rstrip("/"),
                username=conf.ecosystem.username,
                password=conf.ecosystem.password,
            ),
        )
        self.victoria = Victoria(
            conf.victoria.endpoint,
            conf.victoria.username,
            conf.victoria.password,
            conf.victoria.exporter_instance,
        )

    def _iteration(self):
        try:
            self.tick()
        except Exception:
            LOG.error("Collection failed; the next tick will retry")

    def tick(self, now=None) -> None:
        settings = self.conf.worker
        observed, bindings = self.ecosystem.bindings()
        now = now or datetime.now(timezone.utc)
        if observed > now + timedelta(seconds=30) or observed < now - timedelta(
            seconds=120
        ):
            raise ValueError("Stale or future ownership observation")
        bindings = [b for b in bindings if b["source_realm_uuid"] == self.host_realm]
        with contexts.Context().session_manager() as conn:
            store = Store(conn, settings.window_seconds)
            if not store.lock():
                return
            store.observe(
                observed, bindings, max_observation_gap=settings.poll_seconds * 2
            )
        deadline = now - timedelta(seconds=settings.delay_seconds)
        # Bound work per tick so a long backlog does not prevent fresh observations.
        for _ in range(12):
            with contexts.Context().session_manager() as conn:
                store = Store(conn, settings.window_seconds)
                if not store.lock():
                    return
                start = store.next_window()
                end = start + store.window
                if end > deadline or end > observed:
                    break
                self._collect(store, start, end)
        with contexts.Context().session_manager() as conn:
            pending = conn.execute(
                "SELECT DISTINCT window_start FROM usage_intervals"
                " WHERE NOT complete AND window_start >= %s"
                " ORDER BY window_start LIMIT 12",
                (now - timedelta(seconds=settings.retry_seconds),),
            ).fetchall()
        for row in pending:
            with contexts.Context().session_manager() as conn:
                store = Store(conn, settings.window_seconds)
                if not store.lock():
                    return
                self._collect(
                    store, row["window_start"], row["window_start"] + store.window
                )

    def _collect(self, store, start, end) -> None:
        margin = self.conf.worker.gap_seconds * 1000
        samples = self.victoria.samples(
            milliseconds(start) - margin, milliseconds(end) + margin
        )
        store.save_window(start, end, samples, margin)
        LOG.info("Stored usage window %s to %s", start.isoformat(), end.isoformat())
