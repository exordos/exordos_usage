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
SERVICE_CONFIG=/etc/exordos_usage/exordos_usage.conf

test -s "$SERVICE_CONFIG"
chmod 600 "$SERVICE_CONFIG"
SDK_MIGRATIONS="$("$USAGE_ROOT/.venv/bin/python3" -c 'import gcl_sdk; print(gcl_sdk.__path__[0] + "/migrations")')"
"$USAGE_ROOT/.venv/bin/ra-apply-migration" \
    --config-file "$SERVICE_CONFIG" --path "$SDK_MIGRATIONS"
"$USAGE_ROOT/.venv/bin/ra-apply-migration" \
    --config-file "$SERVICE_CONFIG" --path "$USAGE_ROOT/migrations"
systemctl restart exordos-usage-user-api exordos-usage-gservice

systemctl enable --now exordos-usage-recovery.timer
