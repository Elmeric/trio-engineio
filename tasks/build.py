# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys

from invoke import UnexpectedExit, task


def poetry_build(c, dir_="dist"):
    """Build release artifacts into the dist/ directory."""
    c.run(f"rm -f {dir_}/*")

    c.run("poetry version")

    try:
        c.run("poetry build")
    except UnexpectedExit:
        print("*** Build failed")
        sys.exit(1)

    c.run(f"ls -l {dir_}/")


@task(default=True)
def build(c, dir_="dist"):
    """Build artifacts in the dist/ directory."""
    print()
    print("=" * 72)
    print(f"Building release artifacts...")

    poetry_build(c, dir_=dir_)

    print("==> done")


def help_(c):
    print("- inv[oke] build: Build artifacts in the dist/ directory")
