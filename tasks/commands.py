# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from pathlib import Path


def black(check=False, diff=False, color=True):
    cmd = "black"
    if check:
        cmd = f"{cmd} --check"
    if diff:
        if color:
            cmd = f"{cmd} --diff --color"
        else:
            cmd = f"{cmd} --diff"
    cmd = f"{cmd} ."
    return cmd


def isort(check=False, diff=False, color=True):
    cmd = "isort"
    if check:
        cmd = f"{cmd} --check"
    if diff:
        cmd = f"{cmd} --diff"
    if color:
        cmd = f"{cmd} --color"
    cmd = f"{cmd} ."
    return cmd


def mypy(show_absolute_path=True):
    cmd = "mypy"
    if not show_absolute_path:
        cmd = f"{cmd} --hide-absolute-path"
    return cmd


def pylint(ignore_tests=False):
    msg_template = "{path}:{line}:{column} - {C} - ({symbol}) - {msg}"
    files = " ".join(
        f"src/{file.name}" for file in Path("src").iterdir() if "__" not in file.name
    )
    tests = "tests" if not ignore_tests else ""
    cmd = f'pylint --msg-template="{msg_template}" -j 0 {files} {tests}'
    return cmd


def pytest(coverage=None, html=False, lcov=False):
    cmd = "pytest"
    if coverage:
        cov_package = "src"
        if isinstance(coverage, str):
            cov_package = coverage
        cmd = f"{cmd} --cov={cov_package}"
        if html:
            cmd = f"{cmd} --cov-report=html:.htmlcov"
        if lcov:
            cmd = f"{cmd} --cov-report=lcov:lcov.info"
    return cmd
