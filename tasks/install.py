# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys
from pathlib import Path

from invoke import UnexpectedExit, task


def poetry_install(c):
    """Create and update the virtualenv, synchronizing it to versions in poetry.lock."""
    print("=" * 72)
    print(f"Setup the virtualenv via Poetry...")

    try:
        c.run("poetry install --sync")
    except UnexpectedExit:
        print("*** Failed to install the virtualenv")
        sys.exit(1)

    print("==> done")


def precommit_install(c):
    """Install pre-commit hooks."""
    print("=" * 72)
    print(f"Installing pre-commit hooks...")

    if Path(".git").is_dir():
        c.run("poetry run pre-commit install")
        print("==> done")
    else:
        print("*** Failed to install pre-commit hooks: not a Git repository")
        sys.exit(1)


@task(default=True)
def install(c):
    """Settting up the virtualenv via Poetry and install pre-commit hooks."""

    print()
    poetry_install(c)

    print()
    precommit_install(c)


def help_(c):
    print(
        "- inv[oke] install: Setup the virtualenv via Poetry and install pre-commit hooks"
    )
