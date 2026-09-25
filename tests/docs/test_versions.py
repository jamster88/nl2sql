"""Every place this repository writes its own version down.

A release bumps fourteen of them, across five languages, and the failure mode
is not subtle: an image whose label says one thing and whose contents are
another, or a `setup.sh` that pulls a tag this checkout is not. The existing
tests pin each declaration to `nl2sql_agent.__version__` one at a time, which
catches a file that drifts. This catches the other half -- a file that was
never in anybody's list.

It earns its place twice over. The published tags do not move any more, so a
correction is a new patch version and a fourteen-file bump rather than a
re-push; and both npm lockfiles now carry a *dependency* at `4.5.0` as well
as the project, so a careless find-and-replace corrupts them in a way that
only `npm ci` notices.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: file -> the pattern that finds the version in it. Anchored, because a
#: lockfile holds a dependency's version as well as the project's.
DECLARATIONS = {
    "agent/nl2sql_agent/__init__.py": r'^__version__ = "([\d.]+)"',
    "review/nl2sql_review/app.py": r'^__version__ = "([\d.]+)"',
    "agent/Dockerfile": r"^ARG AGENT_VERSION=([\d.]+)",
    "review/Dockerfile": r"^ARG REVIEW_VERSION=([\d.]+)",
    "gui/Dockerfile": r'org\.opencontainers\.image\.version="([\d.]+)"',
    "review/gui/Dockerfile": r'org\.opencontainers\.image\.version="([\d.]+)"',
    "desktop/Dockerfile": r'org\.opencontainers\.image\.version="([\d.]+)"',
    "desktop/pom.xml": r"^  <version>([\d.]+)</version>",
    "gui/package.json": r'^  "version": "([\d.]+)"',
    "review/gui/package.json": r'^  "version": "([\d.]+)"',
}

#: The lockfiles, which say it twice and are read as JSON rather than by
#: pattern -- `packages[""]` is the project itself and everything else in
#: there belongs to somebody on npm.
LOCKFILES = ("gui/package-lock.json", "review/gui/package-lock.json")


def _tracked(*patterns: str) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", *patterns],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return {path for path in result.stdout.split("\0") if path}


@pytest.mark.parametrize("path", sorted(DECLARATIONS))
def test_every_declared_version_is_this_one(path: str):
    found = re.search(DECLARATIONS[path], (REPO_ROOT / path).read_text(), re.MULTILINE)
    assert found, f"{path} no longer declares a version where this test looks"
    assert found.group(1) == __version__


@pytest.mark.parametrize("path", LOCKFILES)
def test_the_lockfiles_say_it_in_both_places(path: str):
    """`npm ci` fails when the two disagree, which is a failing image build
    rather than a failing test -- and a slow way to learn it."""
    data = json.loads((REPO_ROOT / path).read_text())

    assert data["version"] == __version__
    assert data["packages"][""]["version"] == __version__


def test_no_project_manifest_is_missing_from_the_list():
    """A component added without being listed here is one that keeps saying
    4.5.0 through every release after it. That is how the list rots: not by
    an entry going wrong, but by a file never joining it.
    """
    missing = _tracked("*package.json", "*pom.xml") - set(DECLARATIONS)

    assert missing == set(), (
        f"these manifests declare a version nothing checks: {sorted(missing)}"
    )


def test_a_lockfile_is_listed_for_every_npm_project():
    assert _tracked("*package-lock.json") == set(LOCKFILES)


def test_the_published_tags_are_this_version():
    """`setup.sh` pins five tags and they all move together. A tag is the
    version with dots turned into underscores, truncated to however many
    components the tag carries -- so `v4_5` is 4.5.x and `v4_5_1` is exactly
    4.5.1, which is what a correction is published as now that a published
    tag does not move.
    """
    setup_sh = (REPO_ROOT / "setup.sh").read_text()
    tags = {
        name: re.search(rf'^{name}="v([\d_]+)"', setup_sh, re.MULTILINE).group(1)
        for name in ("AGENT_TAG", "GUI_TAG", "REVIEW_TAG", "REVIEW_GUI_TAG", "DESKTOP_TAG")
    }

    assert len(set(tags.values())) == 1, f"the published tags have drifted apart: {tags}"
    tagged = next(iter(tags.values())).split("_")
    assert tagged == __version__.split(".")[: len(tagged)], (
        f"setup.sh pulls v{'_'.join(tagged)}, but this checkout is {__version__}"
    )
