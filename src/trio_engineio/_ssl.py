# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import ssl

import certifi


def default_ssl_context(verify: bool = True) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])

    if verify:
        context.load_verify_locations(certifi.where())
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return context
