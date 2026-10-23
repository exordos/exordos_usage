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

from gcl_iam import opts as iam_opts
from oslo_config import cfg
from restalchemy.common import config_opts

from exordos_usage import version
from exordos_usage.common import constants

CONF = cfg.CONF

worker_opts = [
    cfg.IntOpt("window_seconds", default=300, min=1),
    cfg.IntOpt("delay_seconds", default=120, min=0),
    cfg.IntOpt("gap_seconds", default=45, min=1),
    cfg.IntOpt("poll_seconds", default=60, min=1),
    cfg.IntOpt("retry_seconds", default=3600, min=0),
]
victoria_opts = [
    cfg.StrOpt("endpoint", required=True),
    cfg.StrOpt("username", required=True),
    cfg.StrOpt("password", required=True, secret=True),
]
ecosystem_opts = [
    cfg.StrOpt("endpoint", required=True),
    cfg.StrOpt("core_endpoint", required=True),
    cfg.StrOpt("username", required=True),
    cfg.StrOpt("password", required=True, secret=True),
    cfg.StrOpt("host_realm", default=None),
]
api_opts = [
    cfg.StrOpt("bind-host", default="127.0.0.1"),
    cfg.PortOpt("bind-port", default=8080),
    cfg.IntOpt("workers", default=1, min=1),
]


def register_opts(conf=CONF):
    config_opts.register_postgresql_db_opts(conf)
    iam_opts.register_iam_cli_opts(conf)
    conf.register_cli_opts(worker_opts, "worker")
    conf.register_cli_opts(victoria_opts, "victoria")
    conf.register_cli_opts(ecosystem_opts, "ecosystem")
    conf.register_cli_opts(api_opts, "user_api")


def parse(args):
    register_opts()
    CONF(args=args, project=constants.GLOBAL_SERVICE_NAME, version=version.version_info)
    if CONF.worker.delay_seconds < CONF.worker.gap_seconds:
        raise ValueError("Collection delay must cover the sample gap")
    return CONF
