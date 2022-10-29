# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import pytest


@pytest.fixture
def mock_time(monkeypatch):
    def _time():
        return "123.456"

    monkeypatch.setattr("trio_engineio.eio_types.time.time", _time)
