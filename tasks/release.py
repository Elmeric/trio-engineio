# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
import re
import sys
from datetime import datetime
from pathlib import Path

from invoke import UnexpectedExit, task


@task(default=True, help={"version": "Version to release ([v|V]x[.y[.z]])"})
def release(c, version):
    """Release a new version and tag the code.

    This updates pyproject.toml and the CHANGELOG.md to reflect the released version,
    then commits those changes and tags the code. Nothing has been pushed or published
    yet, so you can always remove the tag (i.e. git tag -d v0.1.29) and revert your
    commit (git reset HEAD~1) if you made a mistake.
    """
    v_pattern = re.compile(r"^[v|V]?(?P<version>\d+\.\d+\.\d+)$", re.ASCII)
    match = v_pattern.match(version)
    if match:
        version = match.group("version")
    else:
        print(f"*** {version} is not a valid version")
        sys.exit(1)

    tag = f"v{version}"
    if len(c.run(f"git tag -l {tag}", hide="out").stdout) != 0:
        print(f"*** Version {tag} already tagged")
        sys.exit(1)

    first_commit = c.run("git rev-list --max-parents=0 HEAD", hide="out").stdout
    earliest_year = c.run(f"git log --pretty=%ci {first_commit}", hide="out").stdout[:4]
    latest_year = c.run("git log -1 --pretty=%ci", hide="out").stdout[:4]
    if earliest_year == latest_year:
        copyright_year = latest_year
    else:
        copyright_year = f"{earliest_year}-{latest_year}"

    default_branch = c.run(
        "git config --get init.defaultBranch", hide="out"
    ).stdout.strip("\n")
    branches = c.run("git branch -a", hide="out").stdout.split("\n")[:-1]
    current_branch = [branch[2:] for branch in branches if branch[0] == "*"][0]
    if current_branch != default_branch:
        print(
            f"*** You are not on {default_branch}; you cannot release from this branch"
        )
        sys.exit(1)

    date_ = datetime.today().strftime("%Y-%m-%d")

    files_to_commit = "LICENSE pyproject.toml CHANGELOG.md"

    commit_msg = f"Release v{version}"

    print()
    print("=" * 72)
    print("Updating CHANGELOG.md...")
    changelog = Path("CHANGELOG.md")
    with changelog.open(mode="r+") as fd:
        changelog_lines = fd.readlines()
        insert_version_at = insert_ref_at = 0
        unreleased_found = False
        for idx, line in enumerate(changelog_lines):
            # print(idx, line)
            if line == "## [Unreleased]\n":
                insert_version_at = idx + 1
            if line.startswith("[unreleased]:"):
                insert_ref_at = idx
            if insert_version_at > 0 and insert_ref_at > 0:
                unreleased_found = True
                break
        if not unreleased_found:
            print(f"*** Unreleased version changes are not at the head of CHANGELOG.md")
            sys.exit(1)
        compare_ref = changelog_lines[insert_ref_at]
        new_version_ref = compare_ref.replace("[unreleased]", f"[{version}]")
        new_version_ref = new_version_ref.replace("HEAD", f"v{version}")
        compare_ref = re.sub(
            r"(v[0-9.]+)(...HEAD\n$)", rf"v{version}\g<2>", compare_ref
        )
        changelog_lines[insert_ref_at] = compare_ref
        changelog_lines.insert(insert_ref_at + 1, new_version_ref)
        changelog_lines.insert(insert_version_at, f"## [{version}] - {date_}\n")
        changelog_lines.insert(insert_version_at, "\n")
        fd.seek(0)
        fd.writelines(changelog_lines)
    print("==> done")

    print()
    print("=" * 72)
    try:
        c.run(f"poetry version {version}")
    except UnexpectedExit:
        print("*** Failed to update version")
        sys.exit(1)
    print("==> done")

    license_file = Path("LICENSE")
    with license_file.open(mode="r") as fd:
        license_lines = fd.readlines()
    copyright_line = license_lines[2]
    license_lines[2] = re.sub(
        r"(^ *Copyright \(c\) *)([0-9-]+)( *.*$)",
        rf"\g<1>{copyright_year}\g<3>",
        copyright_line,
    )
    with license_file.open(mode="w") as fd:
        fd.writelines(license_lines)

    print()
    print("=" * 72)
    print("Diffing files to commit...")
    c.run(f"git diff {files_to_commit}")
    print("==> done")

    print()
    print("=" * 72)
    print("Commiting...")
    try:
        c.run(f'git commit --no-verify -m "{commit_msg}" {files_to_commit}')
    except UnexpectedExit:
        print("*** Tag step failed")
        sys.exit(1)
    print("==> done")

    print()
    print("=" * 72)
    print(f"Tagging commit with {tag}...")
    try:
        c.run(f'git tag -a "{tag}" -m "{commit_msg}"')
    except UnexpectedExit:
        print("*** Tag step failed")
        sys.exit(1)
    print("==> done")

    print()
    print(
        f"*** Version v{version} has been released and committed: you may now review "
        f"your Changelog ('CHANGELOG.md') and publish with 'invoke publish'."
    )
    print(
        f"*** Nothing has been pushed or published yet: you can remove the tag with "
        f"'git tag -d v{version}' and revert your commit with 'git reset HEAD~1' and "
        f"'git restore {files_to_commit}'"
    )
    print()


def help_(c):
    print("- inv[oke] release <version>: Release a new version and tag the code")
