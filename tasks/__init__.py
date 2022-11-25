# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

# Workaround for invoke issue #833 ########################################
# https://github.com/pyinvoke/invoke/issues/833#issuecomment-1293148106
import inspect

if not hasattr(inspect, "getargspec"):
    inspect.getargspec = inspect.getfullargspec
###########################################################################

from invoke import Collection, task

from tasks import build, checks, format, install, publish, release, test

BASIC_TASKS = (install, format, checks, build, test)
ADDITIONAL_TASKS = (release, publish)


@task(default=True)
def welcome(c):
    print()
    print("----------------------------------------")
    print("- Shortcuts for common developer tasks -")
    print("----------------------------------------")
    print()

    print("Basic tasks:")
    print("-----------")
    print()
    for t in BASIC_TASKS:
        t.help_(c)

    print()
    print("Additional tasks:")
    print("----------------")
    print()
    for t in ADDITIONAL_TASKS:
        t.help_(c)


ns = Collection()
ns.add_task(welcome, default=True)
ns.add_collection(checks)
ns.add_collection(format)
ns.add_collection(test)
ns.add_collection(build)
ns.add_collection(publish)
ns.add_collection(install)
ns.add_collection(release)
