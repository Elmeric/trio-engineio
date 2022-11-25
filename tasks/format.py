# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from invoke import task

import tasks.commands as commands


@task
def black(c):
    """Run the black code formatter."""
    cmd = commands.black(check=False, diff=False, color=True)

    print("=" * 72)
    print("Running black formatter...")
    c.run(cmd)
    print("==> done")


@task
def isort(c):
    """Run the isort import formatter."""
    cmd = commands.isort(check=False, diff=False, color=True)

    print("=" * 72)
    print("Running isort formatter...")
    c.run(cmd)
    print("==> done")


@task(name="all", default=True)
def format_(c):
    """Run the code formatters."""

    print()
    black(c)

    print()
    isort(c)


def help_(c):
    print("- inv[oke] format: Run the code formatters")
