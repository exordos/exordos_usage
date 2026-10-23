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

from types import SimpleNamespace
from unittest.mock import Mock

from exordos_usage.services.gservice import Worker


def worker():
    return Worker(
        SimpleNamespace(
            worker=SimpleNamespace(poll_seconds=60),
            ecosystem=SimpleNamespace(
                host_realm=None,
                endpoint="http://eco",
                core_endpoint="http://core",
                username="usage",
                password="test",
            ),
            victoria=SimpleNamespace(
                endpoint="http://victoria",
                username="usage",
                password="test",
                exporter_instance="core",
            ),
        )
    )


def test_failed_iteration_allows_next_collection(caplog):
    service = worker()
    service.tick = Mock(side_effect=[RuntimeError("private details"), None])
    service._loop_iteration()
    service._loop_iteration()
    assert service.tick.call_count == 2
    assert "next tick will retry" in caplog.text
    assert "private details" not in caplog.text


def test_service_start_runs_collection_and_stops():
    service = worker()
    service.should_subscribe_signals = False
    service.tick = Mock(side_effect=service.stop)
    service.start()
    service.tick.assert_called_once_with()
    assert service._stop_event.is_set()
