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

from gcl_looper.services import basic
from restalchemy.common import contexts

from exordos_usage.common.clients import Victoria
from exordos_usage.user_api.dm.models import Store
from exordos_usage.user_api.dm.usage import milliseconds

LOG = logging.getLogger(__name__)


class RecoveryWorker(basic.BasicService):
    """One recovery pass; scheduling belongs to the systemd timer."""

    def __init__(self, conf):
        super().__init__()
        self.conf = conf
        self.failed = False
        self.victoria = Victoria(
            conf.victoria.endpoint, conf.victoria.username, conf.victoria.password
        )

    def _iteration(self):
        try:
            self.run_once()
        except Exception:
            self.failed = True
            LOG.exception("Usage recovery failed")
        finally:
            self.stop()

    def run_once(self, now=None):
        now = now or datetime.now(timezone.utc)
        seconds = self.conf.worker.window_seconds
        end = datetime.fromtimestamp(
            milliseconds(now) // (seconds * 1000) * seconds, tz=timezone.utc
        )
        start = end - timedelta(days=self.conf.recovery.lookback_days)
        with contexts.Context().session_manager() as conn:
            pending = Store(conn, seconds).recovery_windows(start, end)
        summary = {"repaired": 0, "incomplete": 0, "failed": 0, "skipped": 0}
        margin = self.conf.worker.gap_seconds * 1000
        for start in pending:
            if self._stop_event.is_set():
                self.failed = True
                break
            end = start + timedelta(seconds=seconds)
            try:
                with contexts.Context().session_manager() as conn:
                    bindings = Store(conn, seconds).bindings_for_window(start, end)
                samples = self.victoria.samples(
                    milliseconds(start) - margin,
                    milliseconds(end) + margin,
                    {binding.resource_uuid for binding in bindings},
                    max_gap_ms=margin,
                )
                with contexts.Context().session_manager() as conn:
                    store = Store(conn, seconds)
                    store.lock(wait=True)
                    # The collector or another recovery process may have finished it.
                    if not store.recovery_windows(start, end):
                        summary["skipped"] += 1
                        continue
                    store.save_window(start, end, samples, margin, repair=True)
                    remaining = store.recovery_windows(start, end)
                summary["incomplete" if remaining else "repaired"] += 1
            except Exception:
                summary["failed"] += 1
                self.failed = True
                LOG.exception("Failed to recover usage window %s", start.isoformat())
        LOG.info("Usage recovery finished: %s", summary)
        return summary
