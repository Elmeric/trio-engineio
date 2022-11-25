# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys

from invoke import UnexpectedExit, task

from .build import poetry_build


@task(default=True)
def publish(c, build_to="dist"):
    """Publish the current code to PyPI and push to GitHub."""
    print()
    print("=" * 72)
    print(f"Publishing the current code...")

    poetry_build(c, dir_=build_to)

    try:
        c.run("poetry publish")
    except UnexpectedExit:
        print("*** Publish step failed")
        sys.exit(1)

    try:
        c.run("git push --follow-tags")
    except UnexpectedExit:
        print("*** Push step failed")
        sys.exit(1)

    print("==> done")


def help_(c):
    print("- inv[oke] publish: Publish the current code to PyPI and push to GitHub")
