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

"""Write the OpenAPI artifact published by the usage element."""

import argparse
from pathlib import Path

import yaml

from exordos_usage.common import openapi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output-dir", default="docs/openapi")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    specification = openapi.build()
    path = output / "openapi_user.yaml"
    path.write_text(yaml.safe_dump(specification, sort_keys=False))
    print(f"{path}: {len(specification['paths'])} paths")


if __name__ == "__main__":
    main()
