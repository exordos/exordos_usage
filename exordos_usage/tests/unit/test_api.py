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

from unittest.mock import Mock
from uuid import uuid4

import pytest
from gcl_iam.exceptions import Forbidden

from exordos_usage.user_api.api.controllers import InvalidUsageQuery, select_project


def caller(project):
    iam = Mock()
    iam.introspection_info.return_value = {
        "project_id": str(project) if project else None
    }
    return iam


def test_project_token_cannot_read_another_project():
    with pytest.raises(Forbidden):
        select_project(caller(uuid4()), str(uuid4()))


def test_project_is_inferred_from_token():
    project = uuid4()
    assert select_project(caller(project), None) == project


def test_global_token_requires_explicit_project():
    with pytest.raises(InvalidUsageQuery):
        select_project(caller(None), None)


def test_missing_permission_prevents_project_selection():
    iam = caller(uuid4())
    iam.enforcer.enforce.side_effect = PermissionError
    with pytest.raises(PermissionError):
        select_project(iam, None)
