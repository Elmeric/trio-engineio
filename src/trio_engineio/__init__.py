# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from .trio_client import EngineIoClient, EngineIoConnectionError

__version__ = "0.1.4"

__all__ = ["__version__", "EngineIoClient", "EngineIoConnectionError"]
