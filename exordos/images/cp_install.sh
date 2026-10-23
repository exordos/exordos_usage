#!/usr/bin/env bash
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
set -euo pipefail

USAGE_ROOT=/opt/exordos_usage
VENV_PATH="$USAGE_ROOT/.venv"
cd "$USAGE_ROOT"
sudo apt-get update
sudo apt-get install -y libev-dev
uv sync --frozen --no-dev

# Create links to the virtual environment commands.
sudo ln -sf "$VENV_PATH/bin/exordos-usage-user-api" /usr/bin/exordos-usage-user-api
sudo ln -sf "$VENV_PATH/bin/exordos-usage-gservice" /usr/bin/exordos-usage-gservice
sudo ln -sf "$VENV_PATH/bin/exordos-usage-openapi-spec" /usr/bin/exordos-usage-openapi-spec

sudo install -d -m 700 /etc/exordos_usage /etc/exordos_usage/exordos_usage.d
sudo cp "$USAGE_ROOT/etc/exordos_usage/logging.yaml" /etc/exordos_usage/
sudo cp "$USAGE_ROOT/etc/systemd/"exordos-usage-*.service /etc/systemd/system/
sudo systemctl enable exordos-usage-user-api exordos-usage-gservice
