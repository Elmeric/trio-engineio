# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from invoke import task

import tasks.commands as commands


@task
def black(c, diff=False, color=True, warn_only=False):
    """Run the black code formatter in check only mode."""
    cmd = commands.black(check=True, diff=diff, color=color)

    print("=" * 72)
    print("Running black formatter...")
    c.run(cmd, warn=warn_only)
    print("==> done")


@task
def isort(c, diff=False, color=True, warn_only=False):
    """Run the isort import formatter in check only mode."""
    cmd = commands.isort(check=True, diff=diff, color=color)

    print("=" * 72)
    print("Running isort formatter...")
    c.run(cmd, warn=warn_only)
    print("==> done")


@task
def mypy(c, show_absolute_path=True, warn_only=False):
    """Run the mypy types checker."""
    cmd = commands.mypy(show_absolute_path)

    print("=" * 72)
    print("Running mypy checks...")
    c.run(cmd, warn=warn_only)
    print("==> done")


@task
def pylint(c, ignore_tests=False, warn_only=False):
    """Run the pylint code linter."""
    cmd = commands.pylint(ignore_tests)

    print("=" * 72)
    print("Running pylint checks...")
    c.run(cmd, warn=warn_only)
    print("==> done")


@task(name="all", default=True, help={"fail_fast": "exit on first failed check."})
def checks(c, fail_fast=True):
    """Run the code checkers."""
    warn = not fail_fast

    print()
    black(c, warn_only=warn)

    print()
    isort(c, warn_only=warn)

    print()
    mypy(c, show_absolute_path=False, warn_only=warn)

    print()
    pylint(c, ignore_tests=True, warn_only=warn)


def help_(c):
    print("- inv[oke] checks: Run the code checkers")
