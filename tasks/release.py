# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
import re
from datetime import datetime
import sys
from pathlib import Path
from invoke import task, UnexpectedExit


def poetry_install(c):
    """Create and update the virtualenv, synchronizing it to versions in poetry.lock.
    """
    print("=" * 72)
    print(f"Setup the virtualenv via Poetry...")

    try:
        c.run("poetry install --sync")
    except UnexpectedExit:
        print("*** Failed to install the virtualenv")
        sys.exit(1)

    print("==> done")


def precommit_install(c):
    """Install pre-commit hooks.
    """
    print("=" * 72)
    print(f"Installing pre-commit hooks...")

    if Path(".git").is_dir():
        c.run("poetry run pre-commit install")
        print("==> done")
    else:
        print("*** Failed to install the virtualenv")
        sys.exit(1)


@task(default=True, help={"version": "Version to release ([v|V]x[.y[.z]])"})
def release(c, version):
    """Release a specific version and tag the code.

    This updates pyproject.toml and the Changelog to reflect the released version, then
    commits those changes and tags the code. Nothing has been pushed or published yet,
    so you can always remove the tag (i.e. git tag -d v0.1.29) and revert your commit
    (git reset HEAD~1) if you made a mistake.
    """
    version = version[1:] if version[0] in ("v", "V") else version
    print(version)

    tag = f"v{version}"
    print(tag)
    if len(c.run(f"git tag -l {tag}", hide="out").stdout) != 0:
        print(f"*** Version {tag} already tagged")
        sys.exit(1)

    first_commit = c.run("git rev-list --max-parents=0 HEAD", hide='out').stdout
    print(first_commit)
    earliest_year = c.run(f"git log --pretty=%ci {first_commit}", hide="out").stdout[:4]
    latest_year = c.run("git log -1 --pretty=%ci", hide="out").stdout[:4]
    print(earliest_year, latest_year)
    if earliest_year == latest_year:
        copyright_year = latest_year
    else:
        copyright_year = f"{earliest_year}-{latest_year}"
    print(copyright_year)

    default_branch = c.run("git config --get init.defaultBranch", hide="out").stdout.strip("\n")
    print(default_branch)
    branches = c.run("git branch -a", hide="out").stdout.split("\n")[:-1]
    print(branches)
    current_branch = [branch[2:] for branch in branches if branch[0] == "*"][0]
    print(current_branch)
    if current_branch != default_branch:
        print(f"*** You are not on {default_branch}; you cannot release from this branch")
        sys.exit(1)

    date_ = datetime.today().strftime('%d %b %Y')
    print(date_)

    files_to_commit = "LICENSE pyproject.toml Changelog"

    commit_msg = f"Release v{version} to PyPI"

    changelog = Path("Changelog")
    with changelog.open() as f:
        firstline = f.readline().strip("\n")
    pattern = re.compile(rf"^[V|v]ersion\s{version}\s*unreleased$")
    if not pattern.match(firstline):
        print(f"*** Unreleased version {version} is not at the head of the Changelog")
        sys.exit(1)

    try:
        c.run(f"poetry version {version}")
    except UnexpectedExit:
        print("*** Failed to update version")
        sys.exit(1)

    with changelog.open(mode="r") as f:
        lines = f.readlines()
    lines[0] = f"Version {version}     {date_}\n"
    with changelog.open(mode="w") as f:
        f.writelines(lines)

    license_file = Path("LICENSE")
    with license_file.open(mode="r") as f:
        lines = f.readlines()
    copyright_line = lines[2]
    print(copyright_line)
    lines[2] = re.sub(r"(^ *Copyright \(c\) *)([0-9,-]+)( *.*$)", rf"\g<1>{copyright_year}\g<3>", copyright_line)
    print(lines[2])
    with license_file.open(mode="w") as f:
        f.writelines(lines)

    c.run(f"git diff {files_to_commit}")

    try:
        c.run(f"git tag -a \"{tag}\" -m \"{commit_msg}\"")
    except UnexpectedExit:
        print("*** Tag step failed")
        sys.exit(1)

    print()
    print(f"*** Version v{version} has been released and committed; you may publish now (invoke publish)")
    print()

    # print()
    # poetry_install(c)
    #
    # print()
    # precommit_install(c)



def help_(c):
    print("- inv[oke] release <version>: Release a specific version and tag the code")
