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

import sys

from gcl_iam import drivers
from gcl_looper.services import bjoern_service, hub
from restalchemy.common import log as infra_log
from restalchemy.storage.sql import engines

from exordos_usage.common import config
from exordos_usage.user_api.api import app


def main():
    conf = config.parse(sys.argv[1:])
    infra_log.configure()
    driver = drivers.HttpDriver(
        conf.iam.iam_endpoint,
        conf.iam.audience,
        conf.iam.hs256_jwks_decryption_key,
    )
    service_hub = hub.ProcessHubService()
    for _ in range(conf.user_api.workers):
        service = bjoern_service.BjoernService(
            wsgi_app=app.build_wsgi_application(driver),
            host=conf.user_api.bind_host,
            port=conf.user_api.bind_port,
            bjoern_kwargs={"reuse_port": True},
        )
        service.add_setup(
            lambda: engines.engine_factory.configure_postgresql_factory(conf)
        )
        service_hub.add_service(service)
    service_hub.start()


if __name__ == "__main__":
    main()
