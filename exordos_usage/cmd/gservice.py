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

from restalchemy.common import log as infra_log
from restalchemy.storage.sql import engines

from exordos_usage.common import config
from exordos_usage.services.gservice import Worker


def main():
    conf = config.parse(sys.argv[1:])
    infra_log.configure()
    collector = Worker(conf)
    collector.add_setup(
        lambda: engines.engine_factory.configure_postgresql_factory(conf)
    )
    collector.start()


if __name__ == "__main__":
    main()
