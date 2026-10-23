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

import yaml
from jinja2 import Environment, StrictUndefined
from oslo_config import cfg

from exordos_usage.common import config

ROOT = Path(__file__).parents[3]


def test_python_options_read_rendered_template(tmp_path, monkeypatch):
    values = {
        "DB_CONNECTION_URL": "postgresql://usage:secret@localhost/usage",
        "VICTORIA_ENDPOINT": "http://vm",
        "VICTORIA_USERNAME": "reader",
        "VICTORIA_PASSWORD": "vm%secret",
        "ECOSYSTEM_ENDPOINT": "http://eco",
        "ECOSYSTEM_USERNAME": "usage",
        "ECOSYSTEM_PASSWORD": "eco-secret",
        "IAM_ENDPOINT": "http://core/v1/iam/clients/test",
        "IAM_AUDIENCE": "test",
        "JWKS_KEY": "key",
    }
    template = Environment(undefined=StrictUndefined).from_string(
        (ROOT / "etc/exordos_usage/exordos_usage.conf.j2").read_text()
    )
    path = tmp_path / "exordos_usage.conf"
    path.write_text(template.render(values))
    conf = cfg.ConfigOpts()
    # gcl_iam's registration helper currently uses the global ConfigOpts.
    monkeypatch.setattr(config.iam_opts.cfg, "CONF", conf)
    config.register_opts(conf)
    conf(["--config-file", str(path)])
    assert conf.db.connection_url == values["DB_CONNECTION_URL"]
    assert conf.victoria.password == "vm%secret"
    assert conf.ecosystem.password == "eco-secret"
    assert conf.recovery.lookback_days == 7
    assert conf.worker.window_seconds == 300
    assert conf.user_api.bind_host == "0.0.0.0"
    assert conf.iam.iam_endpoint == values["IAM_ENDPOINT"]


def test_logging_yaml_uses_console_at_info_level():
    logging = yaml.safe_load((ROOT / "etc/exordos_usage/logging.yaml").read_text())
    assert logging["version"] == 1
    assert logging["root"]["level"] == "INFO"
    assert logging["handlers"]["console"]["class"] == "logging.StreamHandler"
