# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys

from invoke import task

import tasks
import tasks.commands as commands


@task(optional=["coverage"], default=True)
def test(c, coverage=None, html=False, lcov=False):
    """Run the unit tests."""
    cmd = commands.pytest(coverage, html, lcov)

    print()
    print("=" * 72)
    code_coverage = " with code coverage" if coverage else ""
    print(f"Running unit tests{code_coverage}...")
    c.run(cmd)
    if html:
        open_cmd = "start" if sys.platform.startswith("win32") else "open"
        c.run(f"{open_cmd} .htmlcov/index.html")
    print("==> done")


@task
def suite(c):
    """Run the complete test suite."""
    print()
    tasks.install.install(c)

    print()
    tasks.checks.checks(c)

    print()
    tasks.build.build(c)

    print()
    test(c, coverage=True)


def help_(c):
    print("- inv[oke] test: Run the unit tests")
    print("- inv[oke] test -c <package>: Run the unit tests with coverage of <package>")
    print(
        "- inv[oke] test -c <package> -h: Run the unit tests with coverage of <package> and open the HTML report"
    )
    print(
        "- inv[oke] suite: Run the complete test suite, as for the GitHub Actions CI build"
    )
