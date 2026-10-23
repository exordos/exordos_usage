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

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from exordos_usage.services.recovery import RecoveryWorker

ROOT = Path(__file__).parents[3]


def worker():
    return RecoveryWorker(
        SimpleNamespace(
            victoria=SimpleNamespace(
                endpoint="http://vm", username="reader", password="secret"
            )
        )
    )


def test_recovery_service_runs_once_and_stops():
    service = worker()
    service.should_subscribe_signals = False
    service.run_once = Mock()
    service.start()
    service.run_once.assert_called_once_with()
    assert service._stop_event.is_set()
    assert not service.failed


def test_recovery_failure_stops_and_marks_unsuccessful(caplog):
    service = worker()
    service.should_subscribe_signals = False
    service.run_once = Mock(side_effect=RuntimeError("recovery failure details"))
    service.start()
    service.run_once.assert_called_once_with()
    assert service.failed
    assert "recovery failure details" in caplog.text


def test_nightly_timer_and_oneshot_service_use_venv_entrypoint():
    timer = (ROOT / "etc/systemd/exordos-usage-recovery.timer").read_text()
    service = (ROOT / "etc/systemd/exordos-usage-recovery.service").read_text()
    install = (ROOT / "exordos/images/install.sh").read_text()
    bootstrap = (ROOT / "exordos/images/bootstrap.sh").read_text()
    assert "OnCalendar=*-*-* 03:00:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/bin/exordos-usage-recovery" in service
    assert "--config-dir /etc/exordos_usage/exordos_usage.d" in service
    assert "Restart=" not in service
    assert '"$VENV_PATH/bin/exordos-usage-recovery"' in install
    assert "exordos-usage-recovery.timer" in install
    assert "enable --now exordos-usage-recovery.timer" in bootstrap
